from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas

from app import models
from app.exports.policy import build_attribution_line


PAGE_WIDTH, PAGE_HEIGHT = letter
MARGIN = 36
TEXT_TOP = PAGE_HEIGHT - MARGIN
IMAGE_TOP = PAGE_HEIGHT - 200
IMAGE_BOTTOM = 120
IMAGE_LEFT = MARGIN
IMAGE_RIGHT = PAGE_WIDTH - MARGIN


def _draw_wrapped(c: canvas.Canvas, text: str, x: float, y: float, width: float, line_height: float = 14.0) -> float:
    words = text.split()
    line = ""
    cursor = y
    for word in words:
        trial = f"{line} {word}".strip()
        if c.stringWidth(trial, "Helvetica", 10) <= width:
            line = trial
            continue
        c.drawString(x, cursor, line)
        cursor -= line_height
        line = word
    if line:
        c.drawString(x, cursor, line)
        cursor -= line_height
    return cursor


def _draw_image(c: canvas.Canvas, image_path: Path) -> None:
    reader = ImageReader(str(image_path))
    width, height = reader.getSize()
    box_width = IMAGE_RIGHT - IMAGE_LEFT
    box_height = IMAGE_TOP - IMAGE_BOTTOM
    ratio = min(box_width / float(width), box_height / float(height))
    draw_w = width * ratio
    draw_h = height * ratio
    x = IMAGE_LEFT + (box_width - draw_w) / 2.0
    y = IMAGE_BOTTOM + (box_height - draw_h) / 2.0
    c.drawImage(reader, x, y, draw_w, draw_h, preserveAspectRatio=True, anchor="c")


def _format_observed_at(value: datetime | None) -> str:
    if not value:
        return "Unknown date"
    if value.tzinfo is None:
        return value.strftime("%Y-%m-%d")
    return value.astimezone(UTC).strftime("%Y-%m-%d")


def render_part_pdf(
    output_path: Path,
    items: list[models.ExportItem],
    images_base_dir: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(output_path), pagesize=letter)

    for item in items:
        c.setFont("Helvetica-Bold", 13)
        c.drawString(MARGIN, TEXT_TOP, item.item_title or f"Observation {item.inat_observation_id}")

        c.setFont("Helvetica", 10)
        y = TEXT_TOP - 18
        y = _draw_wrapped(c, f"Observation: {item.inat_url}", MARGIN, y, PAGE_WIDTH - (MARGIN * 2))
        if item.observed_at:
            y = _draw_wrapped(c, f"Observed at: {item.observed_at.isoformat()}", MARGIN, y, PAGE_WIDTH - (MARGIN * 2))

        if item.local_image_relpath:
            image_path = images_base_dir / item.local_image_relpath
            if image_path.exists():
                try:
                    _draw_image(c, image_path)
                except Exception:
                    _draw_wrapped(
                        c,
                        "Image could not be rendered. View source link above.",
                        MARGIN,
                        IMAGE_TOP - 16,
                        PAGE_WIDTH - (MARGIN * 2),
                    )
        else:
            _draw_wrapped(
                c,
                "No local image available. View source link above.",
                MARGIN,
                IMAGE_TOP - 16,
                PAGE_WIDTH - (MARGIN * 2),
            )

        c.setFont("Helvetica", 8)
        attribution = build_attribution_line(
            observation_id=item.inat_observation_id,
            observation_url=item.inat_url,
            attribution_text=item.image_attribution,
            license_code=item.image_license_code,
        )
        _draw_wrapped(c, attribution, MARGIN, 72, PAGE_WIDTH - (MARGIN * 2), line_height=10.0)

        c.showPage()

    c.save()


def render_empty_county_guide_pdf(
    output_path: Path,
    list_title: str,
    reason: str | None = None,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(output_path), pagesize=letter)

    c.setFont("Helvetica-Bold", 15)
    c.drawString(MARGIN, TEXT_TOP, "County Guide")

    c.setFont("Helvetica", 11)
    y = TEXT_TOP - 22
    y = _draw_wrapped(c, f"List: {list_title}", MARGIN, y, PAGE_WIDTH - (MARGIN * 2))
    y = _draw_wrapped(
        c,
        "No exportable county guide pages were available at build time.",
        MARGIN,
        y,
        PAGE_WIDTH - (MARGIN * 2),
    )
    y = _draw_wrapped(
        c,
        "Check observations_index.pdf for the linked observation list and metadata.",
        MARGIN,
        y,
        PAGE_WIDTH - (MARGIN * 2),
    )
    if reason:
        _draw_wrapped(c, f"Build note: {reason}", MARGIN, y - 4, PAGE_WIDTH - (MARGIN * 2))

    c.save()


def render_observation_index_pdf(
    output_path: Path,
    list_title: str,
    observations: list[models.Observation],
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(output_path), pagesize=letter)

    page_no = 1

    def start_page() -> float:
        c.setFont("Helvetica-Bold", 14)
        c.drawString(MARGIN, TEXT_TOP, "DNA-Confirmed Observations Index")

        c.setFont("Helvetica", 10)
        y_pos = TEXT_TOP - 18
        y_pos = _draw_wrapped(c, f"List: {list_title}", MARGIN, y_pos, PAGE_WIDTH - (MARGIN * 2))
        y_pos = _draw_wrapped(
            c,
            "Offline note: this PDF can be viewed offline. External iNaturalist links require internet access.",
            MARGIN,
            y_pos,
            PAGE_WIDTH - (MARGIN * 2),
        )
        c.setFont("Helvetica", 8)
        c.drawRightString(PAGE_WIDTH - MARGIN, 24, f"Page {page_no}")
        return y_pos - 6

    y = start_page()

    for idx, obs in enumerate(observations, start=1):
        title = obs.scientific_name or obs.species_guess or obs.taxon_name or f"Observation {obs.inat_observation_id}"
        common = obs.common_name or "Not provided"
        observer = obs.user_name or "Unknown observer"
        observed_text = _format_observed_at(obs.observed_at)

        if y < 132:
            c.showPage()
            page_no += 1
            y = start_page()

        c.setFont("Helvetica-Bold", 11)
        y = _draw_wrapped(c, f"{idx}. {title}", MARGIN, y, PAGE_WIDTH - (MARGIN * 2))
        c.setFont("Helvetica", 10)
        y = _draw_wrapped(c, f"Observed: {observed_text} | Observer: {observer}", MARGIN, y, PAGE_WIDTH - (MARGIN * 2))
        y = _draw_wrapped(c, f"Common name: {common}", MARGIN, y, PAGE_WIDTH - (MARGIN * 2))
        y = _draw_wrapped(c, f"iNaturalist: {obs.inat_url}", MARGIN, y, PAGE_WIDTH - (MARGIN * 2))
        y -= 4

    if not observations:
        c.setFont("Helvetica", 10)
        _draw_wrapped(c, "No DNA-confirmed observations were cached at build time.", MARGIN, y, PAGE_WIDTH - (MARGIN * 2))

    c.save()
