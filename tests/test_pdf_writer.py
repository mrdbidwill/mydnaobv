from datetime import datetime

from pypdf import PdfReader

from app import models
from app.exports.pdf_writer import (
    render_empty_county_guide_pdf,
    render_observation_index_pdf,
    render_part_pdf,
)


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


def test_render_part_pdf_includes_placeholder_image_note(tmp_path):
    output = tmp_path / "part_001.pdf"
    item = models.ExportItem(
        job_id=1,
        sequence=1,
        inat_observation_id=123,
        inat_url="https://www.inaturalist.org/observations/123",
        item_title="1. Amanita",
        status="rendered",
        skip_reason="placeholder:image_unavailable_in_build",
    )

    render_part_pdf(
        output_path=output,
        items=[item],
        images_base_dir=tmp_path,
    )

    text = "\n".join((page.extract_text() or "") for page in PdfReader(str(output)).pages)
    assert "could not be downloaded in this build" in text


def test_render_part_pdf_includes_no_image_url_note(tmp_path):
    output = tmp_path / "part_002.pdf"
    item = models.ExportItem(
        job_id=1,
        sequence=1,
        inat_observation_id=124,
        inat_url="https://www.inaturalist.org/observations/124",
        item_title="2. Russula",
        status="rendered",
        skip_reason="no_image_url",
    )

    render_part_pdf(
        output_path=output,
        items=[item],
        images_base_dir=tmp_path,
    )

    text = "\n".join((page.extract_text() or "") for page in PdfReader(str(output)).pages)
    assert "No image URL was available in this build" in text
