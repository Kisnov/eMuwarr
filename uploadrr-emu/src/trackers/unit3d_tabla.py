from typing import Any, Dict

from src.trackers.UNIT3D import Unit3d
from src.utils.ids_tracker import cargar_ids, ids_por_token


class Unit3dConTabla(Unit3d):
    """Un UNIT3D de los que sube eMuwarr: sus ids salen de `data/trackers/<tracker>.json` y el nombre es el de eMuwarez.

    La tabla se lee al definir la subclase: sin ella, o mal escrita, el módulo del tracker no se importa y
    `load_trackers` no lo carga (nunca se sube con un "0" inventado). Un id que la tabla no tiene es el de `defaults`:
    `checks.pre_upload_checks` no sube ni busca duplicados con un tipo 0.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        tabla = cargar_ids(cls.tracker)
        cls.defaults = {seccion: str(valor) for seccion, valor in tabla["defaults"].items()}
        cls.category_ids = ids_por_token(tabla, "categories", str.upper)
        cls.resolution_ids = ids_por_token(tabla, "resolutions", str.lower)
        cls.type_ids = ids_por_token(tabla, "types", lambda t: t.upper().replace("-", ""))
        cls.resolution_groups = [[str(i) for i in grupo] for grupo in tabla["search"]["resolution_groups"]]

    async def get_cat_id(self, meta: Dict[str, Any]) -> str:
        return self.category_ids.get(meta["category"], self.defaults["categories"])

    async def get_res_id(self, meta: Dict[str, Any]) -> str:
        return self.resolution_ids.get(meta["resolution"], self.defaults["resolutions"])

    async def get_type_id(self, meta: Dict[str, Any]) -> str:
        return self.type_ids.get(meta["type"], self.defaults["types"])

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
