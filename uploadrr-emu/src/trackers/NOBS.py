from src.trackers.unit3d_tabla import Unit3dConTabla


class NOBS(Unit3dConTabla):
    """N.O.B.S (Nuclear Order Bit Syndicate). Ids de data/trackers/NOBS.json, los de su formulario de subida
    (docs/trackers-ids.md): resoluciones de UNIT3D de serie (576i y 480i con id propio, sin 540p) y sin tipo SD."""

    tracker = "NOBS"
    source_flag = "NOBS"
    domain = "https://nobs.rawsmoke.net"
    comentario_torrent = "Torrent descargado de N.O.B.S."
    allowed_imghosts = []
    banned_groups = [""]
