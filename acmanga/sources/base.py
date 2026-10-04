from abc import ABC, abstractmethod


class MangaSource(ABC):
    key = "base"
    name = "Base"

    @abstractmethod
    def search(self, query):
        raise NotImplementedError

    def details(self, ref, force=False):
        """Resuelve metadatos de una ficha. Las fuentes pueden sobrescribirlo."""
        ident = ref.get("id") if isinstance(ref, dict) else ref
        return {
            "source": self.key,
            "id": str(ident or ""),
            "title": "",
            "author": "",
            "aliases": [],
            "year": "",
            "status": "",
            "type": "",
            "genres": [],
            "language": "en",
            "ref": {"id": str(ident or "")},
        }

    @abstractmethod
    def chapters(self, ref, offset=0, limit=100, force=False):
        raise NotImplementedError

    def chapters_all(self, ref, page_size=100, max_pages=100, force=False):
        """Obtiene todo lo que una fuente expone, paginando si es necesario."""
        items = []
        seen = set()
        offset = 0
        total = None
        for page_index in range(max_pages):
            batch = self.chapters(ref, offset=offset, limit=page_size, force=force and page_index == 0)
            batch_items = batch.get("items") or []
            if total is None:
                try:
                    total = int(batch.get("total") or 0)
                except (TypeError, ValueError):
                    total = 0
            added = 0
            for item in batch_items:
                marker = str(item.get("id") or "")
                if marker and marker in seen:
                    continue
                if marker:
                    seen.add(marker)
                items.append(item)
                added += 1
            next_offset = batch.get("next_offset")
            try:
                next_offset = int(next_offset)
            except (TypeError, ValueError):
                next_offset = offset + len(batch_items)
            if not batch_items or next_offset <= offset:
                break
            offset = next_offset
            if total and offset >= total:
                break
            if added == 0 and len(batch_items) < page_size:
                break
        return {"items": items, "total": len(items), "next_offset": len(items)}

    @abstractmethod
    def prepare_pages(self, chapter, chapter_dir):
        raise NotImplementedError

    def invalidate(self, ref=None):
        """Invalida caches internos de la fuente."""
        return None

    def diagnostic(self):
        return {"source": self.key, "ok": True, "detail": "cargada"}
