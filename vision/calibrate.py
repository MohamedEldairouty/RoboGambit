"""
One-time board calibration tool.

Run from the project root:
    python -m vision.calibrate                # camera 0, no rotation
    python -m vision.calibrate --cam 1        # external USB camera
    python -m vision.calibrate --rotate 90    # camera mounted on the right

Click the four corners of the board in this order:
    1. top-left  (a8 from white's perspective)
    2. top-right (h8)
    3. bottom-right (h1)
    4. bottom-left  (a1)

Keys: r = reset, s = save, q = quit without saving.
"""
import argparse
import json
import os
import sys

import cv2
import numpy as np

from config.settings import FRAME_ROTATION

# Make the script runnable both as `python -m vision.calibrate`
# and as `python vision/calibrate.py`.
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from config.settings import CAMERA_INDEX, SQDICT_PATH


def parse_args():
    p = argparse.ArgumentParser(description="RoboGambit board calibration")
    p.add_argument("--cam", type=int, default=CAMERA_INDEX,
                   help=f"Camera index (default: {CAMERA_INDEX} from config)")
    p.add_argument("--rotate", type=int, default=0, choices=[0, 90, 180, 270],
                   help="CW rotation of board relative to camera "
                        "(right-mounted camera = 90, behind black = 180, left = 270)")
    p.add_argument("--out", default=SQDICT_PATH,
                   help=f"Output JSON path (default: {SQDICT_PATH})")
    return p.parse_args()


def remap_index(r_disp, c_disp, cam_rot):
    """Remap (row, col) from camera view to standard chess notation."""
    if cam_rot == 0:
        return r_disp, c_disp
    if cam_rot == 90:
        return c_disp, 7 - r_disp
    if cam_rot == 180:
        return 7 - r_disp, 7 - c_disp
    if cam_rot == 270:
        return 7 - c_disp, r_disp
    return r_disp, c_disp


def main():
    args = parse_args()
    cam_rot = args.rotate
    out_path = args.out

    points = []

    def mouse_click(event, x, y, flags, param):
        if event == cv2.EVENT_LBUTTONDOWN:
            if len(points) < 4:
                points.append((x, y))
                print(f"[INFO] Point {len(points)}: ({x}, {y})")
            else:
                print("[INFO] Already have 4 points. Press 'r' to reset or 's' to save.")

    cap = cv2.VideoCapture(args.cam)
    if not cap.isOpened():
        print(f"[ERROR] Could not open camera at index {args.cam}")
        sys.exit(1)

    cv2.namedWindow("Board Calibration")
    cv2.setMouseCallback("Board Calibration", mouse_click)

    print("=" * 60)
    print(" RoboGambit Board Calibration")
    print("=" * 60)
    print(f" Camera index: {args.cam}")
    print(f" Rotation:     {cam_rot}° CW")
    print(f" Output:       {out_path}")
    print()
    print(" Click the 4 corners in order:")
    print("   1) top-left  2) top-right  3) bottom-right  4) bottom-left")
    print(" Keys: r=reset  s=save  q=quit")
    print("=" * 60)

    files = "abcdefgh"
    ranks = "87654321"
    font = cv2.FONT_HERSHEY_SIMPLEX

    while True:
        ret, frame = cap.read()
        if not ret:
            continue
        if FRAME_ROTATION == 90:
            frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        elif FRAME_ROTATION == 180:
            frame = cv2.rotate(frame, cv2.ROTATE_180)
        elif FRAME_ROTATION == 270:
            frame = cv2.rotate(frame, cv2.ROTATE_90_COUNTERCLOCKWISE)

        vis = frame.copy()

        for idx, p in enumerate(points):
            cv2.circle(vis, p, 6, (0, 0, 255), -1)
            cv2.putText(vis, str(idx + 1), (p[0] + 8, p[1] - 8),
                        font, 0.7, (0, 255, 0), 2)

        if len(points) == 4:
            pts = np.array(points, dtype=np.int32)
            cv2.polylines(vis, [pts], True, (255, 255, 255), 2)

            src = np.array([[0, 0], [8, 0], [8, 8], [0, 8]], dtype=np.float32)
            dst = np.array(points, dtype=np.float32)
            H = cv2.getPerspectiveTransform(src, dst)

            src_grid = np.array(
                [[[x, y] for x in range(9)] for y in range(9)], dtype=np.float32
            )
            dst_grid = cv2.perspectiveTransform(
                src_grid.reshape(-1, 1, 2), H
            ).reshape(9, 9, 2)

            for r in range(9):
                cv2.polylines(vis, [dst_grid[r, :, :].astype(int)], False, (180, 180, 180), 1)
            for c in range(9):
                cv2.polylines(vis, [dst_grid[:, c, :].astype(int)], False, (180, 180, 180), 1)

            for r in range(8):
                for c in range(8):
                    r_std, c_std = remap_index(r, c, cam_rot)
                    label = f"{files[c_std]}{ranks[r_std]}"
                    center = dst_grid[r, c] + (dst_grid[r + 1, c + 1] - dst_grid[r, c]) / 2
                    cx, cy = int(center[0]), int(center[1])
                    cv2.putText(vis, label, (cx - 12, cy + 5),
                                font, 0.5, (0, 255, 255), 1, cv2.LINE_AA)

        cv2.imshow("Board Calibration", vis)
        key = cv2.waitKey(1) & 0xFF

        if key == ord("q"):
            print("[INFO] Exiting without saving.")
            break

        if key == ord("r"):
            points = []
            print("[INFO] Points reset.")
            continue

        if key == ord("s"):
            if len(points) != 4:
                print("[WARN] You must click 4 points before saving.")
                continue

            src = np.array([[0, 0], [8, 0], [8, 8], [0, 8]], dtype=np.float32)
            dst = np.array(points, dtype=np.float32)
            H = cv2.getPerspectiveTransform(src, dst)

            src_grid = np.array(
                [[[x, y] for x in range(9)] for y in range(9)], dtype=np.float32
            )
            dst_grid = cv2.perspectiveTransform(
                src_grid.reshape(-1, 1, 2), H
            ).reshape(9, 9, 2)

            squares_std = {}
            for r in range(8):
                for c in range(8):
                    tl = dst_grid[r, c].tolist()
                    tr = dst_grid[r, c + 1].tolist()
                    br = dst_grid[r + 1, c + 1].tolist()
                    bl = dst_grid[r + 1, c].tolist()
                    r_std, c_std = remap_index(r, c, cam_rot)
                    name = f"{files[c_std]}{ranks[r_std]}"
                    squares_std[name] = [tl, tr, br, bl]

            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "w") as f:
                json.dump(squares_std, f, indent=2)
            print(f"[OK] Saved {len(squares_std)} squares to {out_path}")
            print(f"     Rotation applied: {cam_rot}°")
            break

    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
