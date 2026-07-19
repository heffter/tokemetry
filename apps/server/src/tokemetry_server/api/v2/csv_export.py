"""RFC 4180 CSV streaming for v2 query responses (Task 66.7, FR-QUERY-009).

A small helper that streams a stable header row plus data rows as CSV. The v2
usage, costs, attempts, and rollups endpoints reuse it when ``format=csv`` is
requested; the range bounds already cap the size. Returned as a ``Response``
subclass so FastAPI skips JSON response-model validation.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Iterable, Sequence
from typing import Any

from fastapi.responses import StreamingResponse

#: The format query value that selects CSV over the default JSON.
CSV_FORMAT = "csv"


def _row_text(writer: Any, buffer: io.StringIO, values: Sequence[Any]) -> str:
    writer.writerow(["" if v is None else v for v in values])
    text = buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    return text


def csv_response(
    filename: str, header: Sequence[str], records: Iterable[Sequence[Any]]
) -> StreamingResponse:
    """Stream ``header`` then ``records`` as RFC 4180 CSV (CRLF line endings)."""

    def generate() -> Iterable[str]:
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\r\n")
        yield _row_text(writer, buffer, header)
        for values in records:
            yield _row_text(writer, buffer, values)

    return StreamingResponse(
        generate(),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
