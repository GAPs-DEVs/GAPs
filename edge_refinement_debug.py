from __future__ import annotations

import argparse
from pathlib import Path

import cv2
import numpy as np

from fretboard_edge_refinement import (
    build_canonical_fretboard,
    fit_string_lines,
    image_line_from_canonical_x_fit,
    normalized_sobel_strength,
    preprocess_warped_frame,
    refine_fret_lines,
    refine_grid_with_edge_lines,
)
from fretboard_mapping import (
    DetectedLine,
    StableFretboardGrid,
    build_grid_from_lines,
    build_grid_from_reference_lines,
    compute_string_crossings,
)


SKIN_COLOR = (88, 132, 190)
STRING_COLOR = (235, 235, 235)
FRET_COLOR = (170, 210, 220)
FITTED_STRING_COLOR = (80, 255, 120)
REFINED_FRET_COLOR = (80, 190, 255)
INTERSECTION_COLOR = (0, 230, 255)
FALLBACK_COLOR = (80, 80, 255)


def interpolate_point(
    start: tuple[float, float],
    end: tuple[float, float],
    ratio: float,
) -> tuple[float, float]:
    return (
        start[0] + (end[0] - start[0]) * ratio,
        start[1] + (end[1] - start[1]) * ratio,
    )


def make_line_between_edges(
    nut_line: DetectedLine,
    reference_line: DetectedLine,
    ratio: float,
    kind: str,
    confidence: float = 1.0,
) -> DetectedLine:
    return DetectedLine(
        kind=kind,
        p0=interpolate_point(nut_line.p0, reference_line.p0, ratio),
        p1=interpolate_point(nut_line.p1, reference_line.p1, ratio),
        confidence=confidence,
    )


def draw_detected_line(
    image: np.ndarray,
    line: DetectedLine,
    color: tuple[int, int, int],
    thickness: int = 2,
) -> None:
    p0 = tuple(int(round(value)) for value in line.p0)
    p1 = tuple(int(round(value)) for value in line.p1)
    cv2.line(image, p0, p1, color, thickness, cv2.LINE_AA)


def make_synthetic_case() -> tuple[np.ndarray, StableFretboardGrid]:
    frame = np.full((540, 960, 3), (34, 38, 40), dtype=np.uint8)

    nut_line = DetectedLine("nut", (170.0, 128.0), (760.0, 108.0), 1.0)
    reference_line = DetectedLine("fret", (250.0, 430.0), (815.0, 390.0), 1.0)
    fret_ratios = (0.16, 0.30, 0.43, 0.55, 0.66, 0.76, 0.86, 1.0)
    fret_lines = [
        make_line_between_edges(nut_line, reference_line, ratio, "fret", confidence=0.96)
        for ratio in fret_ratios
    ]
    grid = build_grid_from_lines(nut_line, fret_lines)

    board_polygon = np.asarray(
        [
            [
                tuple(int(round(value)) for value in nut_line.p0),
                tuple(int(round(value)) for value in nut_line.p1),
                tuple(int(round(value)) for value in reference_line.p1),
                tuple(int(round(value)) for value in reference_line.p0),
            ]
        ],
        dtype=np.int32,
    )
    cv2.fillPoly(frame, board_polygon, (54, 65, 61), cv2.LINE_AA)

    for fret_line in [nut_line, *fret_lines]:
        draw_detected_line(frame, fret_line, FRET_COLOR, thickness=2)

    string_ratios = (0.10, 0.255, 0.415, 0.575, 0.735, 0.89)
    for ratio in string_ratios:
        string_line = DetectedLine(
            "string",
            p0=interpolate_point(nut_line.p0, nut_line.p1, ratio),
            p1=interpolate_point(reference_line.p0, reference_line.p1, ratio),
            confidence=1.0,
        )
        draw_detected_line(frame, string_line, STRING_COLOR, thickness=1)

    # Occlude several middle segments so the test resembles fingers covering part of the board.
    cv2.ellipse(frame, (455, 245), (66, 46), -12, 0, 360, SKIN_COLOR, -1, cv2.LINE_AA)
    cv2.ellipse(frame, (565, 303), (58, 48), 18, 0, 360, SKIN_COLOR, -1, cv2.LINE_AA)
    cv2.rectangle(frame, (640, 205), (725, 290), SKIN_COLOR, -1, cv2.LINE_AA)

    noise = np.random.default_rng(7).normal(0.0, 4.0, frame.shape).astype(np.int16)
    noisy = np.clip(frame.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return noisy, grid


def parse_line(value: str, kind: str) -> DetectedLine:
    parts = [float(part.strip()) for part in value.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("line must be x0,y0,x1,y1")
    return DetectedLine(kind=kind, p0=(parts[0], parts[1]), p1=(parts[2], parts[3]), confidence=1.0)


def load_image_case(args: argparse.Namespace) -> tuple[np.ndarray, StableFretboardGrid]:
    image_path = Path(args.image).expanduser().resolve()
    frame = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if frame is None:
        raise ValueError(f"Could not read image: {image_path}")

    if args.nut is None or args.reference is None:
        raise ValueError("--image mode requires --nut x0,y0,x1,y1 and --reference x0,y0,x1,y1")

    nut_line = parse_line(args.nut, "nut")
    reference_line = parse_line(args.reference, "fret")
    grid = build_grid_from_reference_lines(
        nut_line=nut_line,
        reference_fret_line=reference_line,
        reference_fret_index=args.reference_fret,
    )
    if grid is None:
        raise ValueError("Could not build a grid from the provided nut/reference lines.")
    return frame, grid


def draw_diagnostics(
    frame: np.ndarray,
    grid: StableFretboardGrid,
    output_path: Path,
    warped_output_path: Path | None = None,
) -> tuple[int, int, int]:
    canonical = build_canonical_fretboard(grid)
    if canonical is None:
        raise ValueError("Could not build canonical fretboard from grid.")

    warped = cv2.warpPerspective(frame, canonical.homography, (canonical.width, canonical.height))
    gray = preprocess_warped_frame(warped)
    vertical_strength = normalized_sobel_strength(gray, dx=1, dy=0)
    horizontal_strength = normalized_sobel_strength(gray, dx=0, dy=1)

    string_lines = fit_string_lines(vertical_strength, canonical.width, canonical.height)
    refined_frets = refine_fret_lines(horizontal_strength, grid, canonical)
    refined_grid = refine_grid_with_edge_lines(frame, grid)
    if refined_grid is None:
        refined_grid = grid

    overlay = frame.copy()
    for fret_line in grid.ordered_fret_lines:
        draw_detected_line(overlay, fret_line, (70, 70, 70), thickness=1)

    fitted_string_count = 0
    for fitted_line in string_lines:
        if fitted_line is None:
            continue
        image_line = image_line_from_canonical_x_fit(fitted_line, canonical)
        if image_line is None:
            continue
        fitted_string_count += 1
        draw_detected_line(overlay, image_line, FITTED_STRING_COLOR, thickness=2)

    refined_fret_count = 0
    for original_line, refined_line in zip(grid.ordered_fret_lines, refined_frets):
        draw_detected_line(overlay, refined_line, REFINED_FRET_COLOR, thickness=2)
        if refined_line != original_line:
            refined_fret_count += 1

    fallback_rows = [compute_string_crossings(line) for line in (grid.nut_line, *grid.ordered_fret_lines)]
    for row in fallback_rows:
        for point in row:
            cv2.circle(overlay, tuple(int(round(value)) for value in point), 3, FALLBACK_COLOR, 1, cv2.LINE_AA)

    intersection_count = 0
    for row in refined_grid.string_crossings:
        for point in row:
            intersection_count += 1
            cv2.circle(
                overlay,
                tuple(int(round(value)) for value in point),
                4,
                INTERSECTION_COLOR,
                -1,
                cv2.LINE_AA,
            )

    cv2.putText(
        overlay,
        f"strings: {fitted_string_count}/6, refined frets: {refined_fret_count}/{len(grid.ordered_fret_lines)}, intersections: {intersection_count}",
        (24, 36),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.8,
        (245, 245, 245),
        2,
        cv2.LINE_AA,
    )
    cv2.imwrite(str(output_path), overlay)

    if warped_output_path is not None:
        warped_debug = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        for fitted_line in string_lines:
            if fitted_line is None:
                continue
            start = (int(round(fitted_line.value_at(0.0))), 0)
            end = (int(round(fitted_line.value_at(float(canonical.height - 1)))), canonical.height - 1)
            cv2.line(warped_debug, start, end, FITTED_STRING_COLOR, 1, cv2.LINE_AA)
        cv2.imwrite(str(warped_output_path), warped_debug)

    return fitted_string_count, refined_fret_count, intersection_count


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Debug edge-based guitar string and fret-crossing refinement."
    )
    parser.add_argument("--image", help="Optional real frame image. Omit to run a synthetic occlusion case.")
    parser.add_argument("--nut", help="Nut line for --image mode: x0,y0,x1,y1")
    parser.add_argument("--reference", help="Last visible/reference fret line for --image mode: x0,y0,x1,y1")
    parser.add_argument("--reference-fret", type=int, default=12, help="Reference fret index for --image mode.")
    parser.add_argument(
        "--output",
        default="edge_refinement_debug_output.png",
        help="Path to save the overlay image.",
    )
    parser.add_argument(
        "--warped-output",
        help="Optional path to save the canonical warped debug image.",
    )
    return parser


def main() -> None:
    args = build_arg_parser().parse_args()
    if args.image:
        frame, grid = load_image_case(args)
    else:
        frame, grid = make_synthetic_case()

    output_path = Path(args.output).expanduser().resolve()
    warped_output_path = Path(args.warped_output).expanduser().resolve() if args.warped_output else None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if warped_output_path is not None:
        warped_output_path.parent.mkdir(parents=True, exist_ok=True)

    fitted_strings, refined_frets, intersections = draw_diagnostics(
        frame=frame,
        grid=grid,
        output_path=output_path,
        warped_output_path=warped_output_path,
    )
    print(f"saved overlay: {output_path}")
    if warped_output_path is not None:
        print(f"saved warped debug: {warped_output_path}")
    print(f"detected strings: {fitted_strings}/6")
    print(f"refined frets: {refined_frets}/{len(grid.ordered_fret_lines)}")
    print(f"intersections: {intersections}")


if __name__ == "__main__":
    main()
