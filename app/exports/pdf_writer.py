from __future__ import annotations

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
