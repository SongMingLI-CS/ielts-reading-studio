from __future__ import annotations

from pathlib import Path

from app.models import ReadingPackage

from .docx_exporter import export_docx, export_workbooks
from .html_exporter import export_html
from .json_exporter import export_json


class ExportService:
    def export_package(
        self,
        package: ReadingPackage,
        directory: str | Path,
        formats: set[str],
    ) -> list[Path]:
        output_dir = Path(directory)
        handlers = {"json": export_json, "html": export_html, "docx": export_docx}
        unknown = formats - handlers.keys()
        if unknown:
            raise ValueError(f"Unsupported export formats: {', '.join(sorted(unknown))}")
        return [
            handlers[format_name](package, output_dir / f"{package.unit.id}.{format_name}")
            for format_name in sorted(formats)
        ]

    def export_workbooks(
        self,
        packages: list[ReadingPackage],
        directory: str | Path,
        size: int,
    ) -> list[Path]:
        return export_workbooks(packages, directory, size=size)
