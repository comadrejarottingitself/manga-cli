class MangaError(Exception):
    pass


class SourceError(MangaError):
    """Error clasificado de una fuente remota.

    kind permite al motor distinguir ausencia real de manga de fallos transitorios,
    respuestas incompatibles o errores del parser sin ocultarlos como "no encontrado".
    """

    def __init__(self, message, source=None, status=None, kind="source", transient=False, context=None):
        super().__init__(message)
        self.source = source
        self.status = status
        self.kind = kind or "source"
        self.transient = bool(transient)
        self.context = context

    def as_dict(self):
        return {
            "message": str(self),
            "source": self.source,
            "status": self.status,
            "kind": self.kind,
            "transient": self.transient,
            "context": self.context,
        }
