from typing import Any, Dict

from src.trackers.UNIT3D import Unit3d
from src.utils.ids_tracker import cargar_ids, ids_por_token

_IDS = cargar_ids("EMU")


class EMU(Unit3d):
    tracker = "EMU"
    source_flag = "eMuwarez"
    domain = "https://emu-warez.com"
    allowed_imghosts = []
    banned_groups = [""]

    # De data/trackers/EMU.json, la tabla que también lee la app. Resolución: el 7 es 540p (no 576i), no hay 480i y
    # el SD entrelazado va como el progresivo. Tipo: el 7 es SD (en UNIT3D, FLAC).
    category_ids = ids_por_token(_IDS, "categories", str.upper)
    resolution_ids = ids_por_token(_IDS, "resolutions", str.lower)
    type_ids = ids_por_token(_IDS, "types", lambda t: t.upper().replace("-", ""))
    resolution_groups = [[str(i) for i in grupo] for grupo in _IDS["search"]["resolution_groups"]]

    async def get_cat_id(self, meta: Dict[str, Any]) -> str:
        return self.category_ids.get(meta["category"], str(_IDS["defaults"]["categories"]))

    async def get_res_id(self, meta: Dict[str, Any]) -> str:
        return self.resolution_ids.get(meta["resolution"], str(_IDS["defaults"]["resolutions"]))

    async def get_type_id(self, meta: Dict[str, Any]) -> str:
        return self.type_ids.get(meta["type"], str(_IDS["defaults"]["types"]))

    def edit_name(self, meta: Dict[str, Any]) -> str:
        built_name = meta["name"]
        title = meta.get("title", "")
        aka = meta.get("aka", "")
        og_title = meta.get("original_title", "")

        if meta.get("original_language", "") in ("es", "spa"):
            emu_name = built_name.replace(title, "").replace(aka, og_title).strip()
        else:
            emu_name = built_name

        # Validate length
        while len(emu_name) > 255:
            emu_name = emu_name.replace(aka, "")
            if len(emu_name) <= 255:
                break

            resolution = meta.get("resolution", "")
            emu_name = emu_name.replace(resolution, "")

            if len(emu_name) <= 255:
                break
            type = meta.get("type", "")
            emu_name = emu_name.replace(type, "")
            if len(emu_name) <= 255:
                break
        return emu_name[:255]
