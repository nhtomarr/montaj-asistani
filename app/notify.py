"""Pipeline ilə bildiriş kanalı (Telegram bot) arasında körpü."""


class _Null:
    def progress(self, job_id: int, text: str): ...
    def finished(self, job_id: int): ...
    def failed(self, job_id: int, err: str): ...
    def confirm(self, job_id: int, targets: list[str]): ...


sink = _Null()


def set_sink(s) -> None:
    global sink
    sink = s
