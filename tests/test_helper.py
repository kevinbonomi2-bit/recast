"""The helper process: heavy work happens there, and the tray process never loads the heavy libraries."""

import shutil
import subprocess
import sys
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication

from recast import helper, jobs
from recast.engine import REGISTRY
from recast.ui.thumbs import make_thumbnail

from test_engine import samples  # noqa: F401  (fixture)


def _wait(cond, seconds=40.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        QCoreApplication.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_what_counts_as_heavy(samples):  # noqa: F811
    assert helper.heavy_file(samples["pdf"]) and helper.heavy_file(Path("shot.jxr"))
    assert not helper.heavy_file(samples["jpg"])
    assert helper.heavy_job(REGISTRY["pdf.compress"], [samples["pdf"]])
    assert helper.heavy_job(REGISTRY["video.trim"], [Path("clip.mp4")])
    assert not helper.heavy_job(REGISTRY["image.compress"], [samples["jpg"]])     # Pillow only: stays in the tray
    assert not helper.heavy_job(REGISTRY["video.compress"], [Path("clip.mp4")])   # FFmpeg does the work
    assert not helper.heavy_job(REGISTRY["any.zip"], [samples["pdf"]])               # zipping a PDF is light


def test_pdf_job_runs_in_helper(samples, tmp_path):  # noqa: F811
    src = shutil.copy(samples["pdf"], tmp_path / "doc.pdf")
    job = jobs.Job(REGISTRY["pdf.compress"], [Path(src)], {})
    done, failed, progress = [], [], []
    job.finished.connect(done.append)
    job.failed.connect(failed.append)
    job.progress.connect(progress.append)
    jobs.start(job)
    assert _wait(lambda: done or failed), "the helper never answered"
    assert not failed and done[0][0].exists() and done[0][0].suffix == ".pdf"
    assert progress and progress[-1] == 1.0


def test_helper_reports_errors(tmp_path):
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"this is not a pdf")
    job = jobs.Job(REGISTRY["pdf.compress"], [bad], {})
    done, failed = [], []
    job.finished.connect(done.append)
    job.failed.connect(failed.append)
    jobs.start(job)
    assert _wait(lambda: done or failed)
    assert failed and not done


def test_pdf_preview_comes_from_helper_and_is_cached(samples):  # noqa: F811
    first = time.perf_counter()
    img = make_thumbnail(samples["pdf"])
    took = time.perf_counter() - first
    assert img is not None and not img.isNull() and img.width() > 50
    facts = helper.preview(samples["pdf"])["info"]
    assert facts["pages"] >= 1
    again = time.perf_counter()
    make_thumbnail(samples["pdf"])
    assert time.perf_counter() - again < min(0.05, took)      # second time: no new process


def test_tray_process_never_loads_the_heavy_libraries(samples):  # noqa: F811
    code = ("import sys, recast.app, recast.jobs\n"
            "from pathlib import Path\n"
            "from recast.ui.thumbs import make_thumbnail\n"
            "from recast import estimate\n"
            f"p = Path(r'{samples['pdf']}')\n"
            "assert make_thumbnail(p) is not None and estimate.inspect(p).pages >= 1\n"
            "heavy = {'pymupdf', 'numpy', 'onnxruntime', 'openpyxl', 'mammoth', 'imagecodecs', 'PySide6.QtMultimedia'}\n"
            "print(sorted(heavy & set(sys.modules)))\n")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                         cwd=Path(__file__).resolve().parents[1])
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "[]"
