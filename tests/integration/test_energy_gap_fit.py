"""v5.46.0: the factory QC presets hold the exciton-trion energy gap on a real spectrum.

HADI51's PL_5 is the PL golden fit's spectrum; the golden fit itself runs a frozen
preset without gaps and is unaffected. Here the *committed* WSe2_QC fits it.
"""

import pytest

from core.paths import PROJECT_ROOT
from modules.spectra.io.preset_store import load_presets
from modules.spectra.models.spectrum import ProcessingSettings, SpectrumFile
from modules.spectra.processing.auto_workflow import execute_auto_workflow
from modules.spectra.processing.fitting import HC_EV_NM
from modules.spectra.processing.parser import parse_spectrum

SPECTRUM = PROJECT_ROOT / "tests/fixtures/golden_fit/HADI51/PL_5.txt"


def test_wse2_qc_holds_trion_25_to_45_mev_below_the_exciton():
    data = parse_spectrum(str(SPECTRUM))
    spectrum = SpectrumFile(filename="PL_5.txt", mode="PL", original_data=data, raw_data=data,
                            processed_data=data, processing_settings=ProcessingSettings())
    result = execute_auto_workflow(spectrum, load_presets()["WSe2_QC"])
    fit = result["fit_result"]
    centre = {p.label: p.center for p in fit.fitted_peaks}
    gap = 1000 * (HC_EV_NM / centre["Exciton"] - HC_EV_NM / centre["Trion"])

    assert result["success"] and fit.converged
    assert 25.0 - 1e-6 <= gap <= 45.0 + 1e-6
    assert fit.r_squared == pytest.approx(1.0, abs=0.01)
