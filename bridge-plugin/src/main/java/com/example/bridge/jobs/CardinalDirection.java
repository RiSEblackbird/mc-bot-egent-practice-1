package com.example.bridge.jobs;

import org.bukkit.util.Vector;

/**
 * 採掘ジョブで扱う方向ベクトルを X/Z の正負４方向へ正規化する列挙型。
 * MineCraft の坑道は通常水平に進むため、ここでは水平 2D のみ許容し、
 * 想定外の入力を検出した場合は例外として扱う。
 */
public enum CardinalDirection {
    POS_X(1, 0, 0, 0, 0, 1),
    NEG_X(-1, 0, 0, 0, 0, 1),
    POS_Z(0, 0, 1, 1, 0, 0),
    NEG_Z(0, 0, -1, 1, 0, 0);

    private final int dx;
    private final int dy;
    private final int dz;
    private final int lateralDx;
    private final int lateralDy;
    private final int lateralDz;

    CardinalDirection(int dx, int dy, int dz, int lateralDx, int lateralDy, int lateralDz) {
        this.dx = dx;
        this.dy = dy;
        this.dz = dz;
        this.lateralDx = lateralDx;
        this.lateralDy = lateralDy;
        this.lateralDz = lateralDz;
    }

    public int dx() {
        return dx;
    }

    public int dy() {
        return dy;
    }

    public int dz() {
        return dz;
    }

    /**
     * 断面の幅方向に走査する際の単位ベクトル。
     * POS_X/NEG_X の場合は Z 方向、POS_Z/NEG_Z の場合は X 方向へ広がる設定とする。
     */
    public Vector lateralVector() {
        return new Vector(lateralDx, lateralDy, lateralDz);
    }

    /**
     * JSON で受け取った方向ベクトルをサニタイズし、正規化された列挙値へ変換する。
     */
    public static CardinalDirection fromComponents(int dx, int dy, int dz) {
        if (dy != 0) {
            throw new IllegalArgumentException("Vertical direction is not supported for tunnel jobs");
        }
        if (dx == 1 && dz == 0) {
            return POS_X;
        }
        if (dx == -1 && dz == 0) {
            return NEG_X;
        }
        if (dz == 1 && dx == 0) {
            return POS_Z;
        }
        if (dz == -1 && dx == 0) {
            return NEG_Z;
        }
        throw new IllegalArgumentException("Direction must be one of the cardinal axes");
    }
}
