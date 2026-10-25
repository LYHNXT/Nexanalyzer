<#
.SYNOPSIS
  Check, push, open (or update) the pull request, and wait for CI -- one command.

.DESCRIPTION
  Run from the repository root on a feature branch with everything committed:

      powershell -ExecutionPolicy Bypass -File scripts\ship.ps1

  1. Refuses on `main`, or with uncommitted changes (they would not be in the PR).
  2. Local gates, strictest first -- nothing is pushed if one fails:
       - golden fit, STRICT 1e-5: Raman (DDAA10 RM_1-3) and PL (HADI51,
         VA1P012504023 PL_5) must reproduce their records exactly as on the
         machine that wrote them. This is the fit-consistency gate; CI can only
         repeat it in portable form (1e-3 / 1 %), because other machines round
         differently.
       - the validated golden set (tests/golden), ruff, and the algorithm-change
         guard against origin/main.
  3. Pushes the branch and opens a PR into main, or reuses the open one. A new
     PR's description is the CHANGELOG lines this branch adds, then the PR
     template's checklist.
  4. Waits for CI's two jobs and prints pass or fail; on a failure, the
     failing lines of the log.

  It never merges: main has no branch protection on this plan, so the PR page's
  green/red beside the Merge button is the gate. See docs/Development_Process.md §4.

.PARAMETER NoWait
  Push and open the PR, but do not wait for CI.
#>
param([switch]$NoWait)

$ErrorActionPreference = "Stop"
$root = (git rev-parse --show-toplevel).Trim()
Set-Location $root

function Step($text) { Write-Host "`n== $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "STOP: $text" -ForegroundColor Red; exit 1 }

$gh = (Get-Command gh -ErrorAction SilentlyContinue).Source
if (-not $gh) { $gh = "C:\Program Files\GitHub CLI\gh.exe" }
if (-not (Test-Path $gh)) { Fail "GitHub CLI (gh) not found. Install it from https://cli.github.com and run 'gh auth login'." }
$py = if (Test-Path "venv\Scripts\python.exe") { "venv\Scripts\python.exe" } else { "python" }

# -------------------------------------------------------------- 1. preconditions
$branch = (git rev-parse --abbrev-ref HEAD).Trim()
if ($branch -eq "main") { Fail "you are on main. Create a branch first: git switch -c <name>" }
$dirty = git status --porcelain --untracked-files=no
if ($dirty) { Fail "uncommitted changes; commit them (or stash) so the PR holds what you tested:`n$($dirty -join "`n")" }

# ------------------------------------------------------------ 2. local gates
Step "Golden fit, strict (Raman + PL fit consistency)"
$env:NEXANALYZER_REQUIRE_GOLDEN = "1"
Remove-Item Env:\NEXANALYZER_GOLDEN_PORTABLE -ErrorAction SilentlyContinue
& $py -m pytest tests/unit/test_golden_fit.py -q -p no:cacheprovider -p no:warnings
if ($LASTEXITCODE -ne 0) {
    Fail ("the fit no longer reproduces the golden record on this machine. If you meant to move the numbers, " +
          "regenerate with 'python tests/unit/test_golden_fit.py --regenerate' and say so in CHANGELOG; otherwise find what changed the fit.")
}

Step "Validated golden set"
& $py -m tests.golden.run --out dist/golden
if ($LASTEXITCODE -ne 0) { Fail "the validated golden set failed; see dist/golden/golden_report.md" }

Step "Lint"
& $py -m ruff check .
if ($LASTEXITCODE -ne 0) { Fail "ruff found errors" }

Step "Algorithm-change guard"
git fetch --quiet origin main
& $py scripts/check_algo_change.py origin/main
if ($LASTEXITCODE -ne 0) { Fail "an algorithm or locked file changed without a version bump / CHANGELOG entry" }

# ------------------------------------------------------------ 3. push + PR
Step "Push $branch"
git push --quiet -u origin $branch
if ($LASTEXITCODE -ne 0) { Fail "git push failed" }

$pr = & $gh pr list --head $branch --state open --json number,url --jq ".[0]" | ConvertFrom-Json
if ($pr) {
    Write-Host "Updated the open PR #$($pr.number): $($pr.url)"
} else {
    $added = git diff origin/main...HEAD -- CHANGELOG.md | Where-Object { $_ -match '^\+(?!\+\+)' } | ForEach-Object { $_.Substring(1) }
    $title = (git log -1 --format=%s).Trim()
    $body = @(
        "## What and why", "",
        ($(if ($added) { $added -join "`n" } else { "(no CHANGELOG lines added on this branch)" })), "",
        ((Get-Content .github/pull_request_template.md -Raw) -replace '(?s)^## What and why.*?(?=## Numbers)', ''),
        "", "Local gates passed before push: golden fit strict (Raman + PL), validated set, ruff, algorithm guard."
    ) -join "`n"
    $bodyFile = New-TemporaryFile
    try {
        Set-Content -Path $bodyFile -Value $body -Encoding utf8
        $url = & $gh pr create --base main --head $branch --title $title --body-file $bodyFile
        if ($LASTEXITCODE -ne 0) { Fail "gh pr create failed" }
    } finally {
        Remove-Item $bodyFile -ErrorAction SilentlyContinue
    }
    Write-Host "Opened $url"
    $pr = & $gh pr list --head $branch --state open --json number,url --jq ".[0]" | ConvertFrom-Json
}

if ($NoWait) { Write-Host "`nNot waiting for CI (-NoWait). Watch it with: gh pr checks $($pr.number) --watch"; exit 0 }

# ------------------------------------------------------------ 4. wait for CI
Step "Waiting for CI on PR #$($pr.number)"
Start-Sleep -Seconds 15   # let GitHub register the run for the new head commit
& $gh pr checks $pr.number --watch --interval 30
$ok = ($LASTEXITCODE -eq 0)
if ($ok) {
    Write-Host "`nCI PASSED on PR #$($pr.number). Review it and merge on GitHub: $($pr.url)" -ForegroundColor Green
    exit 0
}
Write-Host "`nCI FAILED on PR #$($pr.number): $($pr.url)" -ForegroundColor Red
$run = & $gh run list --branch $branch --limit 1 --json databaseId --jq ".[0].databaseId"
if ($run) {
    & $gh run view $run --log-failed 2>$null |
        Select-String -Pattern "FAILED|Error|AssertionError|drift|STOP|exit code" |
        Select-Object -First 30 | ForEach-Object { $_.Line }
}
exit 1
