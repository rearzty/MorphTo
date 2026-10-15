"""Built-in converters. Each module exposes one or more Converter subclasses."""

from __future__ import annotations

from morphto.core.registry import Registry


def register_builtin(registry: Registry) -> None:
    from morphto.converters.archives import ArchiveConverter
    from morphto.converters.data import DataConverter
    from morphto.converters.extra_images import JxlConverter, RawConverter, SvgConverter
    from morphto.converters.images import HeifWriter, PillowConverter
    from morphto.converters.media import FFmpegConverter
    from morphto.converters.office import LibreOfficeConverter, MsOfficeConverter, office_families
    from morphto.converters.pdf import PdfiumConverter
    from morphto.converters.richtext import PdfBookConverter, RichTextConverter

    registry.add(PillowConverter())
    registry.add(HeifWriter())
    registry.add(JxlConverter())
    registry.add(SvgConverter())
    registry.add(RawConverter())
    registry.add(FFmpegConverter())
    registry.add(PdfiumConverter())
    registry.add(DataConverter())
    registry.add(RichTextConverter())
    registry.add(PdfBookConverter())
    registry.add(ArchiveConverter())
    registry.add(LibreOfficeConverter())
    families = office_families()
    if families:
        registry.add(MsOfficeConverter(families))
