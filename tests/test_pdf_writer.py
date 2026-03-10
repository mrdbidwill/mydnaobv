from datetime import datetime

from pypdf import PdfReader

from app import models
from app.exports.pdf_writer import render_empty_county_guide_pdf, render_observation_index_pdf


def test_render_empty_county_guide_pdf_creates_file(tmp_path):
    output = tmp_path / "all_observations.pdf"
    render_empty_county_guide_pdf(
        output_path=output,
        list_title="Autauga County-AL",
        reason="No exportable observations were available.",
    )
    assert output.exists()
    assert output.stat().st_size > 0


def test_render_observation_index_pdf_embeds_clickable_inaturalist_link(tmp_path):
    output = tmp_path / "observations_index.pdf"
    url = "https://www.inaturalist.org/observations/193663788"
    obs = models.Observation(
        list_id=1,
        inat_observation_id=193663788,
        inat_url=url,
        scientific_name="Cortinarius iodes",
        common_name="Viscid Violet Cort",
        user_name="David Wilkins",
        observed_at=datetime(2023, 12, 10),
    )

    render_observation_index_pdf(
        output_path=output,
        list_title="Pike County-AL",
        observations=[obs],
    )

    reader = PdfReader(str(output))
    annots = reader.pages[0].get("/Annots")
    assert annots is not None

    uris: list[str] = []
    for annot_ref in annots:
        annot = annot_ref.get_object()
        action = annot.get("/A")
        if action and action.get("/URI"):
            uris.append(str(action["/URI"]))

    assert url in uris
