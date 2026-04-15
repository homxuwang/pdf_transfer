import argparse
import io
import json
import os
import re
import shutil
import subprocess
import threading
import time
import traceback
import zipfile
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader, PdfWriter
from reportlab.lib.utils import simpleSplit
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas


UPLOAD_URL_API = "https://mineru.net/api/v4/file-urls/batch"
RESULT_API_TEMPLATE = "https://mineru.net/api/v4/extract-results/batch/{batch_id}"
DEFAULT_POLL_INTERVAL = 10
SUCCESS_STATES = {"done", "success", "completed"}
FAILED_STATES = {"failed", "error"}
FONT_NAME = "STSong-Light"
_FONT_REGISTERED = False
HTTP_CODE_MARKER = "__HTTP_CODE__:"


def clear_proxy_env() -> None:
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.pop(key, None)


def emit_log(message: str, logger=None) -> None:
    if logger:
        logger(message)
    else:
        print(message, flush=True)


def ensure_font_registered() -> None:
    global _FONT_REGISTERED
    if _FONT_REGISTERED:
        return
    pdfmetrics.registerFont(UnicodeCIDFont(FONT_NAME))
    _FONT_REGISTERED = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Use MinerU API to convert a PDF into a searchable text-layer PDF."
    )
    parser.add_argument("input_pdf", help="Path to the source PDF.")
    parser.add_argument(
        "--token",
        default=os.environ.get("MINERU_API_TOKEN"),
        help="MinerU API token. Defaults to MINERU_API_TOKEN environment variable.",
    )
    parser.add_argument(
        "--output-pdf",
        help="Path to the output searchable PDF. Defaults to '<input>.searchable.pdf'.",
    )
    parser.add_argument(
        "--output-dir",
        help="Directory to store MinerU raw results. Defaults to './mineru_output/<input-stem>'.",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=DEFAULT_POLL_INTERVAL,
        help="Polling interval in seconds.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=7200,
        help="Maximum wait time for MinerU processing in seconds.",
    )
    parser.add_argument(
        "--result-json",
        help="Reuse an existing MinerU result.json and skip upload/polling.",
    )
    return parser.parse_args()


def require_token(token: str | None) -> str:
    if token:
        return token
    raise SystemExit("Missing MinerU token. Pass --token or set MINERU_API_TOKEN.")


def api_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def request_upload_url(session: requests.Session, token: str, pdf_path: Path) -> tuple[str, str]:
    payload = {"files": [{"name": pdf_path.name}]}
    response = session.post(UPLOAD_URL_API, headers=api_headers(token), json=payload, timeout=60)
    response.raise_for_status()
    payload = response.json()
    if payload.get("code") != 0:
        raise RuntimeError(f"MinerU upload-url request failed: {payload}")

    data = payload["data"]
    return data["batch_id"], data["file_urls"][0]


def upload_pdf_with_curl(upload_url: str, pdf_path: Path, logger=None) -> None:
    curl_path = shutil.which("curl.exe") or shutil.which("curl")
    if not curl_path:
        raise RuntimeError("curl.exe not found, cannot use curl fallback for upload.")

    emit_log(f"[upload] falling back to curl: {curl_path}", logger)
    start = time.time()
    process = subprocess.Popen(
        [
            curl_path,
            "--progress-bar",
            "--show-error",
            "--write-out",
            f"\n{HTTP_CODE_MARKER}:%{{http_code}}",
            "--request",
            "PUT",
            "--http1.1",
            "--retry",
            "2",
            "--retry-all-errors",
            "--connect-timeout",
            "60",
            "--max-time",
            "7200",
            "-T",
            str(pdf_path),
            upload_url,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )

    stderr_chunks: list[str] = []
    progress_state = {"last_percent": -1, "last_heartbeat": time.time()}

    def handle_progress_text(text: str) -> None:
        cleaned = text.strip()
        if not cleaned:
            return

        stderr_chunks.append(cleaned)
        match = re.search(r"(\d{1,3}(?:\.\d+)?)%", cleaned)
        if match:
            percent = int(float(match.group(1)))
            if percent > progress_state["last_percent"]:
                progress_state["last_percent"] = percent
                emit_log(f"[upload] progress: {percent}%", logger)
                progress_state["last_heartbeat"] = time.time()
        elif "curl:" in cleaned.lower():
            emit_log(f"[upload] curl: {cleaned}", logger)

    def read_stderr() -> None:
        if process.stderr is None:
            return

        buffer = ""
        while True:
            char = process.stderr.read(1)
            if not char:
                break
            if char in ("\r", "\n"):
                handle_progress_text(buffer)
                buffer = ""
            else:
                buffer += char
        handle_progress_text(buffer)

    stderr_thread = threading.Thread(target=read_stderr, daemon=True)
    stderr_thread.start()

    while process.poll() is None:
        time.sleep(5)
        elapsed = time.time() - start
        if time.time() - progress_state["last_heartbeat"] >= 5:
            percent = progress_state["last_percent"]
            if percent >= 0:
                emit_log(f"[upload] still uploading... {percent}% ({elapsed:.0f}s)", logger)
            else:
                emit_log(f"[upload] still uploading... ({elapsed:.0f}s)", logger)
            progress_state["last_heartbeat"] = time.time()

    stdout = process.stdout.read() if process.stdout is not None else ""
    stderr_thread.join(timeout=2)
    stderr = "\n".join(stderr_chunks).strip()
    if f"{HTTP_CODE_MARKER}:" in stdout:
        body, http_code = stdout.rsplit(f"{HTTP_CODE_MARKER}:", 1)
        http_code = http_code.strip()
        body = body.strip()
    else:
        http_code = ""
        body = stdout.strip()
    if process.returncode != 0:
        detail = (stderr or body)[:1500]
        raise RuntimeError(f"curl upload failed with exit code {process.returncode}: {detail}")
    if http_code not in {"200", "201"}:
        detail = (body or stderr)[:1500]
        raise RuntimeError(f"curl upload failed with HTTP {http_code}: {detail}")
    emit_log(f"[upload] curl upload completed in {time.time() - start:.1f}s", logger)


def upload_pdf(session: requests.Session, upload_url: str, pdf_path: Path, logger=None) -> None:
    size = pdf_path.stat().st_size
    emit_log(f"[upload] file size: {size / 1024 / 1024:.2f} MB", logger)

    curl_path = shutil.which("curl.exe") or shutil.which("curl")
    if curl_path:
        emit_log("[upload] using curl first, matching MinerU doc example", logger)
        upload_pdf_with_curl(upload_url, pdf_path, logger=logger)
        return

    def file_chunks(chunk_size: int = 8 * 1024 * 1024):
        sent = 0
        last_log_at = time.time()
        with pdf_path.open("rb") as handle:
            while True:
                chunk = handle.read(chunk_size)
                if not chunk:
                    break
                sent += len(chunk)
                now = time.time()
                if sent == size or now - last_log_at >= 3:
                    emit_log(
                        f"[upload] sent {sent / 1024 / 1024:.2f} / {size / 1024 / 1024:.2f} MB",
                        logger,
                    )
                    last_log_at = now
                yield chunk

    start = time.time()
    try:
        response = session.put(
            upload_url,
            data=file_chunks(),
            headers={"Content-Length": str(size)},
            timeout=(60, 3600),
        )
    except requests.exceptions.RequestException as exc:
        emit_log(f"[upload] requests upload failed: {exc}", logger)
        upload_pdf_with_curl(upload_url, pdf_path, logger=logger)
        return
    if response.status_code not in (200, 201):
        snippet = response.text[:500].strip()
        raise RuntimeError(f"OSS upload failed with HTTP {response.status_code}: {snippet}")
    emit_log(f"[upload] completed in {time.time() - start:.1f}s", logger)


def poll_result(
    session: requests.Session,
    token: str,
    batch_id: str,
    poll_interval: int,
    timeout_seconds: int,
    logger=None,
) -> dict:
    url = RESULT_API_TEMPLATE.format(batch_id=batch_id)
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        response = session.get(url, headers=api_headers(token), timeout=60)
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"MinerU result polling failed: {payload}")

        results = payload.get("data", {}).get("extract_result", [])
        if results:
            current = results[0]
            status = (current.get("status") or current.get("state") or "").lower()
            emit_log(f"[poll] status={status or 'pending'}", logger)

            if status in SUCCESS_STATES:
                return payload
            if status in FAILED_STATES:
                raise RuntimeError(f"MinerU task failed: {json.dumps(current, ensure_ascii=False)}")
        else:
            emit_log("[poll] waiting for extract_result...", logger)

        time.sleep(poll_interval)

    raise TimeoutError(f"Timed out after {timeout_seconds} seconds while waiting for batch {batch_id}.")


def save_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_existing_result(result_json_path: Path) -> dict:
    return json.loads(result_json_path.read_text(encoding="utf-8"))


def download_file_with_resume(
    session: requests.Session,
    url: str,
    destination: Path,
    max_retries: int = 5,
    chunk_size: int = 8 * 1024 * 1024,
    logger=None,
) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and zipfile.is_zipfile(destination):
        emit_log(f"[download] using existing zip: {destination}", logger)
        return

    for attempt in range(1, max_retries + 1):
        existing_size = destination.stat().st_size if destination.exists() else 0
        headers = {"Range": f"bytes={existing_size}-"} if existing_size else {}
        emit_log(
            f"[download] attempt {attempt}/{max_retries}, existing={existing_size / 1024 / 1024:.2f} MB",
            logger,
        )

        try:
            with session.get(url, headers=headers, stream=True, timeout=(60, 3600)) as response:
                if existing_size and response.status_code == 200:
                    destination.unlink(missing_ok=True)
                    existing_size = 0
                elif existing_size and response.status_code != 206:
                    response.raise_for_status()

                response.raise_for_status()
                mode = "ab" if existing_size else "wb"
                with destination.open(mode) as handle:
                    downloaded = existing_size
                    last_log_at = time.time()
                    for chunk in response.iter_content(chunk_size=chunk_size):
                        if chunk:
                            handle.write(chunk)
                            downloaded += len(chunk)
                            now = time.time()
                            if now - last_log_at >= 3:
                                emit_log(f"[download] received {downloaded / 1024 / 1024:.2f} MB", logger)
                                last_log_at = now
            emit_log(f"[download] completed: {destination}", logger)
            return
        except requests.exceptions.RequestException as exc:
            if attempt == max_retries:
                raise RuntimeError(f"Download failed after {max_retries} attempts: {exc}") from exc
            emit_log(f"[download] attempt {attempt}/{max_retries} failed, retrying: {exc}", logger)
            time.sleep(min(5 * attempt, 20))


def download_and_extract_zip(session: requests.Session, zip_url: str, output_dir: Path, logger=None) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    zip_path = output_dir / "mineru_result.zip"
    download_file_with_resume(session, zip_url, zip_path, logger=logger)

    extract_dir = output_dir / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)
    emit_log(f"[extract] extracting zip to {extract_dir}", logger)
    with zipfile.ZipFile(zip_path, "r") as archive:
        archive.extractall(extract_dir)
    emit_log("[extract] extraction completed", logger)
    return extract_dir


def find_first(base_dir: Path, name: str) -> Path | None:
    matches = sorted(base_dir.rglob(name))
    return matches[0] if matches else None


def html_table_to_text(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    rows = []
    for tr in soup.find_all("tr"):
        cells = [cell.get_text(" ", strip=True) for cell in tr.find_all(["th", "td"])]
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def load_content_blocks(extracted_dir: Path) -> list[dict]:
    content_list_path = find_first(extracted_dir, "content_list.json")
    if content_list_path:
        return json.loads(content_list_path.read_text(encoding="utf-8"))

    layout_path = find_first(extracted_dir, "layout.json")
    if layout_path:
        layout_payload = json.loads(layout_path.read_text(encoding="utf-8"))
        pages = layout_payload.get("pdf_info", [])
        blocks: list[dict] = []
        for page_index, page in enumerate(pages):
            page_blocks = page.get("para_blocks") or page.get("preproc_blocks") or []
            for block in page_blocks:
                bbox = block.get("bbox")
                if not isinstance(bbox, list) or len(bbox) < 4:
                    continue
                text = block_to_text_from_layout(block)
                if not text:
                    continue
                blocks.append({"page_idx": page_index, "bbox": bbox, "text": text})
        return blocks

    raise FileNotFoundError("MinerU result package does not contain content_list.json or layout.json.")


def line_text_from_layout(line: dict) -> str:
    parts = []
    for span in line.get("spans", []):
        span_type = span.get("type")
        if span_type in {"text", "inline_equation"}:
            parts.append(str(span.get("content") or ""))
        elif span_type == "table":
            html = span.get("html") or ""
            if html:
                parts.append(html_table_to_text(html))
    return "".join(parts).strip()


def block_to_text_from_layout(block: dict) -> str:
    if block.get("lines"):
        lines = [line_text_from_layout(line) for line in block["lines"]]
        return "\n".join(line for line in lines if line)

    if block.get("blocks"):
        texts = [block_to_text_from_layout(child) for child in block["blocks"]]
        return "\n".join(text for text in texts if text)

    return ""


def block_to_text(block: dict) -> str:
    block_type = (block.get("type") or "").lower()
    text = block.get("text") or ""

    if block_type in {"header", "footer", "page_number"} and not text.strip():
        return ""

    if block_type == "table":
        html = block.get("html") or ""
        if html:
            text = html_table_to_text(html)

    if block_type == "image":
        caption = " ".join(block.get("img_caption", [])).strip()
        footnote = " ".join(block.get("img_footnote", [])).strip()
        text = "\n".join([item for item in (caption, footnote) if item])

    if block_type == "table":
        caption = " ".join(block.get("table_caption", [])).strip()
        footnote = " ".join(block.get("table_footnote", [])).strip()
        extras = [item for item in (caption, footnote) if item]
        if extras:
            text = "\n".join([part for part in (text, *extras) if part])

    if isinstance(text, list):
        text = "\n".join(str(item) for item in text if str(item).strip())

    return " ".join(str(text).split()) if "\n" not in str(text) else "\n".join(
        line.strip() for line in str(text).splitlines() if line.strip()
    )


def page_index_of(block: dict) -> int | None:
    for key in ("page_idx", "page_index", "page_no", "page_num"):
        value = block.get(key)
        if isinstance(value, int):
            return value
    return None


def scale_bbox(bbox: list[float], page_width: float, page_height: float) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = [float(v) for v in bbox[:4]]
    if x1 <= page_width * 1.2 and y1 <= page_height * 1.2:
        return x0, y0, x1, y1

    max_value = max(abs(x0), abs(y0), abs(x1), abs(y1))
    if max_value <= 1000.5:
        return (
            page_width * x0 / 1000.0,
            page_height * y0 / 1000.0,
            page_width * x1 / 1000.0,
            page_height * y1 / 1000.0,
        )

    return min(x0, page_width), min(y0, page_height), min(x1, page_width), min(y1, page_height)


def fit_text_lines(text: str, box_width: float, box_height: float) -> tuple[list[str], float]:
    text = text.strip()
    if not text or box_width <= 1 or box_height <= 1:
        return [], 0.0

    paragraphs = [part.strip() for part in text.splitlines() if part.strip()] or [text]
    font_size = min(max(box_height * 0.45, 6.0), 16.0)

    while font_size >= 4.0:
        lines: list[str] = []
        for paragraph in paragraphs:
            wrapped = simpleSplit(paragraph, FONT_NAME, font_size, box_width)
            lines.extend(wrapped or [""])

        leading = font_size * 1.15
        if lines and len(lines) * leading <= box_height + font_size * 0.3:
            return lines, font_size
        font_size -= 0.5

    tiny_size = 4.0
    lines = []
    for paragraph in paragraphs:
        wrapped = simpleSplit(paragraph, FONT_NAME, tiny_size, box_width)
        lines.extend(wrapped or [""])
    return lines, tiny_size


def create_overlay_page(blocks: list[dict], page_width: float, page_height: float) -> PdfReader | None:
    usable_blocks = []
    for block in blocks:
        bbox = block.get("bbox")
        if not isinstance(bbox, list) or len(bbox) < 4:
            continue
        text = block_to_text(block)
        if not text:
            continue
        usable_blocks.append((bbox, text))

    if not usable_blocks:
        return None

    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=(page_width, page_height))

    for bbox, text in usable_blocks:
        x0, y0, x1, y1 = scale_bbox(bbox, page_width, page_height)
        box_width = max(x1 - x0, 1.0)
        box_height = max(y1 - y0, 1.0)
        lines, font_size = fit_text_lines(text, box_width, box_height)
        if not lines:
            continue

        top_y = page_height - y0
        bottom_limit = page_height - y1
        leading = font_size * 1.15
        start_y = top_y - font_size

        text_obj = pdf.beginText()
        text_obj.setTextRenderMode(3)
        text_obj.setFont(FONT_NAME, font_size)
        text_obj.setLeading(leading)
        text_obj.setTextOrigin(x0, start_y)

        current_y = start_y
        for line in lines:
            if current_y < bottom_limit - leading:
                break
            text_obj.textLine(line)
            current_y -= leading

        pdf.drawText(text_obj)

    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return PdfReader(buffer)


def build_searchable_pdf(source_pdf: Path, content_list: list[dict], output_pdf: Path) -> None:
    reader = PdfReader(str(source_pdf))
    writer = PdfWriter()

    pages_to_blocks: dict[int, list[dict]] = {}
    for block in content_list:
        page_index = page_index_of(block)
        if page_index is None:
            continue
        pages_to_blocks.setdefault(page_index, []).append(block)

    for index, page in enumerate(reader.pages):
        writer.add_page(page)
        blocks = pages_to_blocks.get(index, [])
        if not blocks:
            continue

        page_width = float(page.mediabox.width)
        page_height = float(page.mediabox.height)
        overlay_reader = create_overlay_page(blocks, page_width, page_height)
        if overlay_reader:
            writer.pages[index].merge_page(overlay_reader.pages[0])

    output_pdf.parent.mkdir(parents=True, exist_ok=True)
    with output_pdf.open("wb") as handle:
        writer.write(handle)


def convert_pdf(
    input_pdf: str | Path,
    token: str | None = None,
    output_pdf: str | Path | None = None,
    output_dir: str | Path | None = None,
    poll_interval: int = DEFAULT_POLL_INTERVAL,
    timeout: int = 7200,
    result_json: str | Path | None = None,
    logger=None,
) -> dict[str, Path]:
    clear_proxy_env()
    ensure_font_registered()

    input_pdf_path = Path(input_pdf).resolve()
    if not input_pdf_path.exists():
        raise FileNotFoundError(f"Input PDF not found: {input_pdf_path}")

    resolved_output_dir = (
        Path(output_dir).resolve()
        if output_dir
        else (Path.cwd() / "mineru_output" / input_pdf_path.stem).resolve()
    )
    resolved_output_pdf = (
        Path(output_pdf).resolve()
        if output_pdf
        else input_pdf_path.with_name(f"{input_pdf_path.stem}.searchable.pdf")
    )

    session = requests.Session()
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    if result_json:
        result_json_path = Path(result_json).resolve()
        emit_log(f"[1/3] Reusing existing MinerU result JSON: {result_json_path}", logger)
        result_payload = load_existing_result(result_json_path)
    else:
        actual_token = require_token(token)
        emit_log(f"[1/5] Requesting upload URL for {input_pdf_path.name}", logger)
        batch_id, upload_url = request_upload_url(session, actual_token, input_pdf_path)
        emit_log(f"[2/5] Uploading PDF to MinerU OSS (batch_id={batch_id})", logger)
        upload_pdf(session, upload_url, input_pdf_path, logger=logger)

        emit_log("[3/5] Polling MinerU extract result", logger)
        result_payload = poll_result(session, actual_token, batch_id, poll_interval, timeout, logger=logger)
        save_json(resolved_output_dir / "result.json", result_payload)

    extract_result = result_payload["data"]["extract_result"][0]
    zip_url = extract_result.get("full_zip_url")
    if not zip_url:
        raise RuntimeError("MinerU completed but no full_zip_url was returned.")

    if result_json:
        emit_log("[2/3] Downloading and extracting MinerU result package", logger)
    else:
        emit_log("[4/5] Downloading and extracting MinerU result package", logger)
    extracted_dir = download_and_extract_zip(session, zip_url, resolved_output_dir, logger=logger)
    content_list = load_content_blocks(extracted_dir)

    if result_json:
        emit_log("[3/3] Building searchable PDF", logger)
    else:
        emit_log("[5/5] Building searchable PDF", logger)
    emit_log(f"[build] overlay text blocks: {len(content_list)}", logger)
    build_searchable_pdf(input_pdf_path, content_list, resolved_output_pdf)

    markdown_path = find_first(extracted_dir, "full.md")
    if markdown_path:
        target_md = resolved_output_dir / "full.md"
        if markdown_path.resolve() != target_md.resolve():
            target_md.write_text(markdown_path.read_text(encoding="utf-8"), encoding="utf-8")

    emit_log(f"Searchable PDF written to: {resolved_output_pdf}", logger)
    emit_log(f"MinerU raw outputs stored in: {resolved_output_dir}", logger)
    return {
        "input_pdf": input_pdf_path,
        "output_pdf": resolved_output_pdf,
        "output_dir": resolved_output_dir,
        "result_json": resolved_output_dir / "result.json",
    }


def main() -> int:
    args = parse_args()
    try:
        convert_pdf(
            input_pdf=args.input_pdf,
            token=args.token,
            output_pdf=args.output_pdf,
            output_dir=args.output_dir,
            poll_interval=args.poll_interval,
            timeout=args.timeout,
            result_json=args.result_json,
        )
        return 0
    except KeyboardInterrupt:
        emit_log("Conversion cancelled by user.")
        return 130
    except Exception as exc:
        emit_log(f"Conversion failed: {exc}")
        emit_log(traceback.format_exc().rstrip())
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
