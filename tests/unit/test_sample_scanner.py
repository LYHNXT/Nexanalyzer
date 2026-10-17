"""Unit tests for modules.spectra.processing.sample_scanner (sample-folder discovery)."""

import pytest

from modules.spectra.processing.sample_scanner import default_magnification, scan_sample_folder


def _touch(path):
    path.write_text("x")


class TestScanSampleFolder:
    def test_buckets_raman_pl_and_images(self, tmp_path):
        for n in range(1, 4):
            _touch(tmp_path / f"Raman_{n}.txt")
            _touch(tmp_path / f"PL_{n}.txt")
            _touch(tmp_path / f"100x_{n}.bmp")

        scan = scan_sample_folder(str(tmp_path))

        assert scan.sample_name == tmp_path.name
        assert set(scan.raman_files.keys()) == {1, 2, 3}
        assert set(scan.pl_files.keys()) == {1, 2, 3}
        assert scan.image_files["100x"].keys() == {1, 2, 3}
        assert scan.ignored_files == []

    def test_raman_and_pl_prefix_case_insensitive(self, tmp_path):
        _touch(tmp_path / "raman_1.txt")
        _touch(tmp_path / "pl_1.txt")

        scan = scan_sample_folder(str(tmp_path))

        assert scan.raman_files == {1: str((tmp_path / "raman_1.txt").resolve())}
        assert scan.pl_files == {1: str((tmp_path / "pl_1.txt").resolve())}

    def test_rm_prefix_with_mixed_separators_and_case(self, tmp_path):
        # Real-world naming seen in practice: RM_1..7.txt, RM-8.txt, rm-9.txt
        for n in range(1, 8):
            _touch(tmp_path / f"RM_{n}.txt")
        _touch(tmp_path / "RM-8.txt")
        _touch(tmp_path / "rm-9.txt")

        scan = scan_sample_folder(str(tmp_path))

        assert set(scan.raman_files.keys()) == set(range(1, 10))

    def test_pl_files_with_unrecognized_naming_are_ignored_not_guessed(self, tmp_path):
        # No "PL" prefix and no trailing point number -> not silently
        # bucketed as PL, even though it's clearly spectral data.
        _touch(tmp_path / "Spectrum--294--Spec.Data 1.txt")

        scan = scan_sample_folder(str(tmp_path))

        assert scan.pl_files == {}
        assert "Spectrum--294--Spec.Data 1.txt" in scan.ignored_files

    def test_distinct_magnifications_kept_separate(self, tmp_path):
        _touch(tmp_path / "100x_1.bmp")
        _touch(tmp_path / "10x_1.bmp")

        scan = scan_sample_folder(str(tmp_path))

        assert set(scan.image_files.keys()) == {"100x", "10x"}
        assert scan.magnifications() == ["100x", "10x"]

    def test_unrelated_files_are_ignored(self, tmp_path):
        _touch(tmp_path / "Raman_1.txt")
        _touch(tmp_path / "VABA52_Analyzed.txt")
        _touch(tmp_path / "notes.md")
        _touch(tmp_path / "project.wip")

        scan = scan_sample_folder(str(tmp_path))

        assert set(scan.raman_files.keys()) == {1}
        assert set(scan.ignored_files) == {"VABA52_Analyzed.txt", "notes.md", "project.wip"}

    def test_partial_points_are_not_an_error(self, tmp_path):
        _touch(tmp_path / "Raman_1.txt")
        _touch(tmp_path / "Raman_5.txt")
        _touch(tmp_path / "Raman_9.txt")

        scan = scan_sample_folder(str(tmp_path))

        assert set(scan.raman_files.keys()) == {1, 5, 9}

    def test_missing_folder_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            scan_sample_folder(str(tmp_path / "does_not_exist"))


class TestDefaultMagnification:
    def test_prefers_100x(self, tmp_path):
        _touch(tmp_path / "100x_1.bmp")
        _touch(tmp_path / "10x_1.bmp")
        scan = scan_sample_folder(str(tmp_path))

        assert default_magnification(scan) == "100x"

    def test_falls_back_to_alphabetical_first(self, tmp_path):
        _touch(tmp_path / "10x_1.bmp")
        _touch(tmp_path / "50x_1.bmp")
        scan = scan_sample_folder(str(tmp_path))

        assert default_magnification(scan) == "10x"

    def test_none_when_no_images(self, tmp_path):
        _touch(tmp_path / "Raman_1.txt")
        scan = scan_sample_folder(str(tmp_path))

        assert default_magnification(scan) is None


class TestMappingFileNames:
    def test_rm_and_pl_mapping_files_count_as_their_technique(self, tmp_path):
        for n in (1, 2):
            _touch(tmp_path / f"RM_MAPPING_{n}.txt")
            _touch(tmp_path / f"PL_MAPPING_{n}.txt")
        scan = scan_sample_folder(str(tmp_path))

        assert sorted(scan.raman_files) == [1, 2] and sorted(scan.pl_files) == [1, 2]
        assert not scan.ignored_files
