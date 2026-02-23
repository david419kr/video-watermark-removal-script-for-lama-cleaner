import argparse
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

INPUT_DIR = Path("./temp/input")
OUTPUT_DIR = Path("./temp/output")
MASK_PATH = Path("mask.png")
PROGRESS_BAR_WIDTH = 50
REQUEST_TIMEOUT_SECONDS = 600

REQUEST_DATA = {
    "ldmSteps": 25,
    "ldmSampler": "plms",
    "hdStrategy": "Original",
    "zitsWireframe": False,
    "hdStrategyCropMargin": 128,
    "hdStrategyCropTrigerSize": 512,
    "hdStrategyResizeLimit": 1280,
    "prompt": "",
    "negativePrompt": "",
    "useCroper": False,
    "croperX": 0,
    "croperY": 0,
    "croperHeight": 512,
    "croperWidth": 512,
    "sdScale": 1.0,
    "sdMaskBlur": 0,
    "sdStrength": 0.75,
    "sdSteps": 50,
    "sdGuidanceScale": 7.5,
    "sdSampler": "uni_pc",
    "sdSeed": 42,
    "sdMatchHistograms": False,
    "cv2Flag": "INPAINT_NS",
    "cv2Radius": 4,
    "paintByExampleSteps": 50,
    "paintByExampleGuidanceScale": 7.5,
    "paintByExampleMaskBlur": 0,
    "paintByExampleSeed": 42,
    "paintByExampleMatchHistograms": False,
    "paintByExampleExampleImage": "",
    "p2pSteps": 50,
    "p2pImageGuidanceScale": 7.5,
    "p2pGuidanceScale": 7.5,
    "controlnet_conditioning_scale": 0.4,
    "controlnet_method": "control_v11p_sd15_canny",
    "paint_by_example_example_image": "",
}


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--instances", type=int, default=1, help="lama-cleaner instance count")
    parser.add_argument("--base-port", type=int, default=8080, help="start port for lama-cleaner instances")
    return parser.parse_args()


def frame_sort_key(path: Path):
    try:
        return 0, int(path.stem)
    except ValueError:
        return 1, path.stem


def collect_frames():
    frames = [path for path in INPUT_DIR.iterdir() if path.is_file()]
    frames.sort(key=frame_sort_key)
    return frames


def split_evenly(items, bucket_count):
    base_size = len(items) // bucket_count
    remainder = len(items) % bucket_count
    chunks = []
    start = 0

    for index in range(bucket_count):
        extra = 1 if index < remainder else 0
        end = start + base_size + extra
        chunks.append(items[start:end])
        start = end

    return chunks


class ProgressPrinter:
    def __init__(self, total):
        self.total = total
        self.done = 0
        self.lock = threading.Lock()
        self._print()

    def step(self):
        with self.lock:
            self.done += 1
            self._print()
            if self.done >= self.total:
                print()

    def _print(self):
        if self.total <= 0:
            percent = 100.0
            filled = PROGRESS_BAR_WIDTH
        else:
            percent = (self.done / self.total) * 100
            filled = int((self.done / self.total) * PROGRESS_BAR_WIDTH)

        bar = ("#" * filled).ljust(PROGRESS_BAR_WIDTH, "-")
        print(
            f"\rcleaning extracted frames... |{bar}| {percent:5.1f}% Complete",
            end="",
            flush=True,
        )


def process_chunk(worker_index, port, frame_paths, mask_bytes, progress):
    session = requests.Session()
    url = f"http://127.0.0.1:{port}/inpaint"

    for frame_path in frame_paths:
        with frame_path.open("rb") as image_file:
            files = {
                "image": (frame_path.name, image_file, "image/jpeg"),
                "mask": ("mask.png", mask_bytes, "image/png"),
            }
            response = session.post(url, files=files, data=REQUEST_DATA, timeout=REQUEST_TIMEOUT_SECONDS)

        if response.status_code != 200:
            text_preview = response.text.replace("\r", " ").replace("\n", " ")
            text_preview = text_preview[:240]
            raise RuntimeError(
                f"Worker {worker_index} failed on port {port}: HTTP {response.status_code} {text_preview}"
            )

        output_path = OUTPUT_DIR / frame_path.name
        with output_path.open("wb") as output_file:
            output_file.write(response.content)

        progress.step()


def main():
    args = parse_args()

    if args.instances < 1:
        print("instances must be >= 1", file=sys.stderr)
        return 1

    if not INPUT_DIR.exists():
        print(f"Input folder not found: {INPUT_DIR}", file=sys.stderr)
        return 1

    if not MASK_PATH.exists():
        print(f"Mask file not found: {MASK_PATH}", file=sys.stderr)
        return 1

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    frames = collect_frames()
    total_frames = len(frames)

    if total_frames == 0:
        print("No extracted frames found in ./temp/input", file=sys.stderr)
        return 1

    ports = [args.base_port + index for index in range(args.instances)]
    chunks = split_evenly(frames, args.instances)
    tasks = []

    print(f"Detected lama-cleaner instances: {args.instances}")
    for index, frame_chunk in enumerate(chunks):
        if not frame_chunk:
            continue
        port = ports[index]
        print(f"  worker {index + 1}: port {port}, frames {len(frame_chunk)}")
        tasks.append((index + 1, port, frame_chunk))

    if not tasks:
        print("No frames assigned to workers.", file=sys.stderr)
        return 1

    mask_bytes = MASK_PATH.read_bytes()
    progress = ProgressPrinter(total_frames)

    errors = []
    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        future_map = {
            executor.submit(process_chunk, worker_id, port, frame_chunk, mask_bytes, progress): (worker_id, port)
            for worker_id, port, frame_chunk in tasks
        }
        for future in as_completed(future_map):
            worker_id, port = future_map[future]
            try:
                future.result()
            except Exception as exc:
                errors.append(f"worker {worker_id} (port {port}): {exc}")

    if errors:
        print("\nFrame cleaning failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Frame cleaning completed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
