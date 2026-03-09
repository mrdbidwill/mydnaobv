from app.exports.pdf_writer import render_empty_county_guide_pdf


def test_render_empty_county_guide_pdf_creates_file(tmp_path):
    output = tmp_path / "all_observations.pdf"
    render_empty_county_guide_pdf(
        output_path=output,
        list_title="Autauga County-AL",
        reason="No exportable observations were available.",
    )
    assert output.exists()
    assert output.stat().st_size > 0
