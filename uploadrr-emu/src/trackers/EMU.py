from src.trackers.unit3d_tabla import Unit3dConTabla


class EMU(Unit3dConTabla):
    """eMuwarez. Ids de data/trackers/EMU.json, la tabla que también lee la app. Resolución: el 7 es 540p (no 576i), no
    hay 480i y el SD entrelazado va como el progresivo. Tipo: el 7 es SD (en UNIT3D, FLAC)."""

    tracker = "EMU"
    source_flag = "eMuwarez"
    domain = "https://emu-warez.com"
    allowed_imghosts = []
    banned_groups = [""]
