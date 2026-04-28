"""
Experimental 6-DOF relative pose estimation for VL53L8CX point clouds.

What this is and isn't:
  This is a small experiment, not a production-grade tracker. The
  VL53L8CX gives 64 zones at 15 Hz with +-10-30 mm noise per zone -
  a sparse, noisy depth map. Frame-to-frame rigid registration on
  this is feasible but will drift. It's a proof of concept for what
  is otherwise the IMU's job in the planned helmet integration.

Algorithm:
  Closed-form rigid registration via the Kabsch / Wahba / Procrustes
  method. Given two paired sets of 3D points (P from frame k-1 and
  Q from frame k), find the rotation R and translation t that
  minimise sum ||Q_i - (R P_i + t)||^2.

  1. Subtract centroids: Pc = P - mean(P), Qc = Q - mean(Q)
  2. Cross-covariance: H = Pc^T Qc
  3. SVD: U, S, V^T = svd(H)
  4. Reflection guard: d = sign(det(V U^T)),  R = V diag(1,1,d) U^T
  5. Translation: t = mean(Q) - R mean(P)
  6. Per-frame transform = (R, t).
  7. Compose into world pose: T_world(k) = T_world(k-1) . delta,
     where delta is the inverse of the fitted (R, t) since the fitted
     transform maps the *world point as observed in the old sensor
     frame* to its observation in the new sensor frame.

Correspondence:
  We use same-zone correspondence (zone i in frame k-1 paired with
  zone i in frame k). This is correct only under the small-motion
  assumption: between two frames at 15 Hz (~67 ms), each zone still
  observes approximately the same world point. For fast motion this
  breaks down and the estimate becomes unreliable - so we gate the
  output on per-frame translation and rotation magnitudes.

Validity gating:
  - Need at least MIN_VALID_POINTS valid zones in both frames.
  - Reject per-frame translations larger than max_translation_mm.
  - Reject per-frame rotations larger than max_rotation_deg.
  Rejected frames break the chain (cumulative pose holds steady)
  rather than corrupt it.
"""

import numpy as np


MIN_VALID_POINTS = 6


def _angle_from_rotation(R):
    """Magnitude of the rotation in radians (axis-angle representation)."""
    cos_theta = (np.trace(R) - 1.0) / 2.0
    cos_theta = float(np.clip(cos_theta, -1.0, 1.0))
    return np.arccos(cos_theta)


def kabsch(P, Q):
    """
    Find R, t such that Q ~= R P + t (least-squares).
    P, Q: (N, 3) arrays of corresponding points.
    Returns (R (3,3), t (3,)).
    """
    centroid_P = P.mean(axis=0)
    centroid_Q = Q.mean(axis=0)
    Pc = P - centroid_P
    Qc = Q - centroid_Q

    H = Pc.T @ Qc
    U, _, Vt = np.linalg.svd(H)
    d = np.sign(np.linalg.det(Vt.T @ U.T))
    D = np.diag([1.0, 1.0, d])
    R = Vt.T @ D @ U.T
    t = centroid_Q - R @ centroid_P
    return R, t


class RelativePoseEstimator:
    """
    Tracks per-frame relative motion of the sensor and integrates
    into a cumulative world pose (R_world, t_world).

    Conventions:
      - Input points are in the sensor's local frame, axes (X, Y, Z)
        with Z forward (depth). The visualiser remaps to GL axes
        outside this class - we work in the sensor's native frame.
      - R_world is the sensor's orientation in the start frame.
      - t_world is the sensor's origin in the start frame.
    """

    def __init__(self, max_translation_mm=300.0, max_rotation_deg=20.0):
        self.max_translation_mm = float(max_translation_mm)
        self.max_rotation_rad   = np.radians(max_rotation_deg)

        self.world_R = np.eye(3)
        self.world_t = np.zeros(3)

        self._prev_points = None     # (64, 3) sensor-frame points from last frame
        self._prev_valid  = None     # (64,) bool

        # Live readouts
        self.last_delta_R       = np.eye(3)
        self.last_delta_t       = np.zeros(3)
        self.last_was_valid     = False
        self.frames_processed   = 0
        self.frames_rejected    = 0

    def reset(self):
        self.world_R = np.eye(3)
        self.world_t = np.zeros(3)
        self._prev_points = None
        self._prev_valid  = None
        self.frames_processed = 0
        self.frames_rejected  = 0

    def update(self, points, valid_mask):
        """
        points:     (64, 3) sensor-frame point cloud for the current frame.
        valid_mask: (64,) bool - True for zones with valid distance.
        Returns True if the cumulative pose was updated this frame.
        """
        points = np.asarray(points, dtype=float)
        valid_mask = np.asarray(valid_mask, dtype=bool)

        if self._prev_points is None or self._prev_valid is None:
            self._prev_points = points.copy()
            self._prev_valid  = valid_mask.copy()
            self.last_was_valid = False
            return False

        # Use only zones valid in BOTH frames (so same-zone correspondence holds)
        common = self._prev_valid & valid_mask
        if int(common.sum()) < MIN_VALID_POINTS:
            self._prev_points = points.copy()
            self._prev_valid  = valid_mask.copy()
            self.last_was_valid = False
            return False

        P = self._prev_points[common]
        Q = points[common]

        R_fit, t_fit = kabsch(P, Q)

        # The fitted (R, t) transforms a world point's old-frame coords
        # into its new-frame coords. The sensor moved by the inverse:
        #   delta_R = R_fit^T
        #   delta_t = -R_fit^T t_fit
        delta_R = R_fit.T
        delta_t = -R_fit.T @ t_fit

        # Sanity gates - reject nonsense (large jumps almost certainly
        # mean wrong correspondence rather than real motion).
        translation_norm = float(np.linalg.norm(delta_t))
        rotation_norm    = _angle_from_rotation(delta_R)
        if (translation_norm > self.max_translation_mm or
            rotation_norm    > self.max_rotation_rad):
            self.frames_rejected += 1
            self._prev_points = points.copy()
            self._prev_valid  = valid_mask.copy()
            self.last_was_valid = False
            return False

        # Compose into cumulative world pose:
        #   T_world(k) = T_world(k-1) . delta
        new_world_R = self.world_R @ delta_R
        new_world_t = self.world_R @ delta_t + self.world_t

        self.world_R = new_world_R
        self.world_t = new_world_t

        self.last_delta_R   = delta_R
        self.last_delta_t   = delta_t
        self.last_was_valid = True
        self.frames_processed += 1

        self._prev_points = points.copy()
        self._prev_valid  = valid_mask.copy()
        return True

    def cumulative_translation_mm(self):
        return float(np.linalg.norm(self.world_t))

    def cumulative_rotation_deg(self):
        return float(np.degrees(_angle_from_rotation(self.world_R)))

    def trail_in_current_frame(self, world_trail):
        """
        Convert a list of past world-frame sensor positions into the
        sensor's CURRENT local frame, so they can be drawn behind the
        sensor in the existing sensor-frame view.

        world_trail: (M, 3) array of past sensor origin positions.
        Returns: (M, 3) in current-sensor-frame coordinates.
        """
        if len(world_trail) == 0:
            return np.zeros((0, 3))
        # p_local = R_world^T (p_world - t_world)
        return (np.asarray(world_trail) - self.world_t) @ self.world_R
