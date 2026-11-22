from src.trackers.unit3d_tabla import Unit3dConTabla


class MNV(Unit3dConTabla):
    """Milnueve. Ids de data/trackers/MNV.json, los de su formulario de subida (docs/trackers-ids.md): resoluciones de
    UNIT3D de serie (576i y 480i con id propio, sin 540p) y sin tipo SD."""

    tracker = "MNV"
    source_flag = "Milnueve"
    domain = "https://tracker.milnueve.cc"
    comentario_torrent = "Torrent descargado de Milnueve."
    allowed_imghosts = []
    banned_groups = [""]
