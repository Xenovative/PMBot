"""
SQLite 持久化層 — 交易、合併、掃描記錄 & 分析查詢
"""
import sqlite3
import os
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional, Dict, Any, List


DB_PATH = os.path.join(os.path.dirname(__file__), "pmbot.db")

_local = threading.local()


def _get_conn() -> sqlite3.Connection:
    """每個線程一個連接（SQLite 不允許跨線程共享）"""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA foreign_keys=ON")
    return _local.conn


def init_db():
    """建立所有資料表（冪等）"""
    conn = _get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT    NOT NULL,
            market_slug TEXT    NOT NULL,
            trade_type  TEXT    NOT NULL DEFAULT 'arbitrage',
            side        TEXT,
            up_price    REAL    DEFAULT 0,
            down_price  REAL    DEFAULT 0,
            total_cost  REAL    DEFAULT 0,
            order_size  REAL    DEFAULT 0,
            profit      REAL    DEFAULT 0,
            profit_pct  REAL    DEFAULT 0,
            status      TEXT    NOT NULL,
            details     TEXT    DEFAULT '',
            created_at  TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS merges (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp       TEXT    NOT NULL,
            market_slug     TEXT    NOT NULL,
            condition_id    TEXT    NOT NULL,
            amount          REAL    DEFAULT 0,
            usdc_received   REAL    DEFAULT 0,
            tx_hash         TEXT    DEFAULT '',
            gas_cost        REAL    DEFAULT 0,
            net_profit      REAL    DEFAULT 0,
            status          TEXT    NOT NULL,
            details         TEXT    DEFAULT '',
            created_at      TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS scans (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT    NOT NULL,
            market_slug TEXT    NOT NULL,
            up_price    REAL    DEFAULT 0,
            down_price  REAL    DEFAULT 0,
            total_cost  REAL    DEFAULT 0,
            spread      REAL    DEFAULT 0,
            up_liquidity REAL   DEFAULT 0,
            down_liquidity REAL DEFAULT 0,
            opportunity_viable INTEGER DEFAULT 0,
            created_at  TEXT    DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS daily_summary (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            date            TEXT    NOT NULL UNIQUE,
            total_trades    INTEGER DEFAULT 0,
            successful      INTEGER DEFAULT 0,
            failed          INTEGER DEFAULT 0,
            total_profit    REAL    DEFAULT 0,
            total_volume    REAL    DEFAULT 0,
            merges          INTEGER DEFAULT 0,
            merge_usdc      REAL    DEFAULT 0,
            scans           INTEGER DEFAULT 0,
            best_trade_profit REAL  DEFAULT 0,
            worst_trade_profit REAL DEFAULT 0,
            avg_spread      REAL    DEFAULT 0,
            created_at      TEXT    DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_trades_timestamp ON trades(timestamp);
        CREATE INDEX IF NOT EXISTS idx_trades_market ON trades(market_slug);
        CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status);
        CREATE INDEX IF NOT EXISTS idx_merges_timestamp ON merges(timestamp);
        CREATE INDEX IF NOT EXISTS idx_scans_timestamp ON scans(timestamp);
        CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_summary(date);
    """)
    conn.commit()


# ─── 寫入 ───

def record_trade(
    timestamp: str,
    market_slug: str,
    trade_type: str = "arbitrage",
    side: str = "BOTH",
    up_price: float = 0,
    down_price: float = 0,
    total_cost: float = 0,
    order_size: float = 0,
    profit: float = 0,
    profit_pct: float = 0,
    status: str = "executed",
    details: str = "",
) -> int:
    conn = _get_conn()
    cur = conn.execute(
        """INSERT INTO trades
           (timestamp, market_slug, trade_type, side, up_price, down_price,
            total_cost, order_size, profit, profit_pct, status, details)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (timestamp, market_slug, trade_type, side, up_price, down_price,
         total_cost, order_size, profit, profit_pct, status, details),
    )
    conn.commit()
    return cur.lastrowid


def record_merge(
    timestamp: str,
    market_slug: str,
    condition_id: str,
    amount: float = 0,
    usdc_received: float = 0,
    tx_hash: str = "",
    gas_cost: float = 0,
    net_profit: float = 0,
    status: str = "success",
    details: str = "",
) -> int:
    conn = _get_conn()
    cur = conn.execute(
        """INSERT INTO merges
           (timestamp, market_slug, condition_id, amount, usdc_received,
            tx_hash, gas_cost, net_profit, status, details)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (timestamp, market_slug, condition_id, amount, usdc_received,
         tx_hash, gas_cost, net_profit, status, details),
    )
    conn.commit()
    return cur.lastrowid


def record_scan(
    timestamp: str,
    market_slug: str,
    up_price: float = 0,
    down_price: float = 0,
    total_cost: float = 0,
    spread: float = 0,
    up_liquidity: float = 0,
    down_liquidity: float = 0,
    opportunity_viable: bool = False,
) -> int:
    conn = _get_conn()
    cur = conn.execute(
        """INSERT INTO scans
           (timestamp, market_slug, up_price, down_price, total_cost,
            spread, up_liquidity, down_liquidity, opportunity_viable)
           VALUES (?,?,?,?,?,?,?,?,?)""",
        (timestamp, market_slug, up_price, down_price, total_cost,
         spread, up_liquidity, down_liquidity, int(opportunity_viable)),
    )
    conn.commit()
    return cur.lastrowid


def rebuild_daily_summary(date_str: Optional[str] = None):
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    conn = _get_conn()
    day_start = f"{date_str}T00:00:00"
    day_end = f"{date_str}T23:59:59"

    row = conn.execute("""
        SELECT
            COUNT(*)                                             AS total_trades,
            SUM(CASE WHEN status IN ('executed','simulated') THEN 1 ELSE 0 END) AS successful,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END)  AS failed,
            COALESCE(SUM(profit), 0)                             AS total_profit,
            COALESCE(SUM(order_size * total_cost), 0)            AS total_volume,
            COALESCE(MAX(profit), 0)                             AS best_trade_profit,
            COALESCE(MIN(profit), 0)                             AS worst_trade_profit
        FROM trades
        WHERE timestamp BETWEEN ? AND ?
    """, (day_start, day_end)).fetchone()

    merge_row = conn.execute("""
        SELECT
            COUNT(*)                            AS merges,
            COALESCE(SUM(usdc_received), 0)     AS merge_usdc
        FROM merges
        WHERE timestamp BETWEEN ? AND ? AND status IN ('success','simulated')
    """, (day_start, day_end)).fetchone()

    scan_row = conn.execute("""
        SELECT COUNT(*) AS scans,
               COALESCE(AVG(spread), 0) AS avg_spread
        FROM scans
        WHERE timestamp BETWEEN ? AND ?
    """, (day_start, day_end)).fetchone()

    conn.execute("""
        INSERT INTO daily_summary
            (date, total_trades, successful, failed, total_profit, total_volume,
             merges, merge_usdc, scans, best_trade_profit, worst_trade_profit, avg_spread)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(date) DO UPDATE SET
            total_trades=excluded.total_trades,
            successful=excluded.successful,
            failed=excluded.failed,
            total_profit=excluded.total_profit,
            total_volume=excluded.total_volume,
            merges=excluded.merges,
            merge_usdc=excluded.merge_usdc,
            scans=excluded.scans,
            best_trade_profit=excluded.best_trade_profit,
            worst_trade_profit=excluded.worst_trade_profit,
            avg_spread=excluded.avg_spread
    """, (
        date_str,
        row["total_trades"], row["successful"], row["failed"],
        row["total_profit"], row["total_volume"],
        merge_row["merges"], merge_row["merge_usdc"],
        scan_row["scans"], row["best_trade_profit"], row["worst_trade_profit"],
        scan_row["avg_spread"],
    ))
    conn.commit()


# ─── 讀取 / 分析 ───

def get_trades(limit: int = 100, offset: int = 0, status: Optional[str] = None) -> List[Dict]:
    conn = _get_conn()
    if status:
        rows = conn.execute(
            "SELECT * FROM trades WHERE status=? ORDER BY id DESC LIMIT ? OFFSET ?",
            (status, limit, offset),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [dict(r) for r in rows]


def get_merges(limit: int = 50) -> List[Dict]:
    conn = _get_conn()
    rows = conn.execute(
        "SELECT * FROM merges ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def get_cumulative_profit(days: int = 30) -> List[Dict]:
    conn = _get_conn()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    rows = conn.execute("""
        SELECT
            strftime('%Y-%m-%dT%H:00:00', timestamp) AS hour,
            SUM(profit) AS hourly_profit,
            SUM(SUM(profit)) OVER (ORDER BY strftime('%Y-%m-%dT%H:00:00', timestamp)) AS cumulative
        FROM trades
        WHERE timestamp >= ? AND status IN ('executed','simulated')
        GROUP BY hour
        ORDER BY hour
    """, (since,)).fetchall()
    return [dict(r) for r in rows]


def get_daily_pnl(days: int = 30) -> List[Dict]:
    conn = _get_conn()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT * FROM daily_summary
        WHERE date >= ?
        ORDER BY date
    """, (since,)).fetchall()
    return [dict(r) for r in rows]


def get_per_market_stats() -> List[Dict]:
    conn = _get_conn()
    rows = conn.execute("""
        SELECT
            market_slug,
            COUNT(*) AS total_trades,
            SUM(CASE WHEN status IN ('executed','simulated') THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS losses,
            COALESCE(SUM(profit), 0) AS total_profit,
            COALESCE(AVG(profit), 0) AS avg_profit,
            COALESCE(MAX(profit), 0) AS best_trade,
            COALESCE(MIN(profit), 0) AS worst_trade,
            COALESCE(AVG(total_cost), 0) AS avg_cost
        FROM trades
        GROUP BY market_slug
        ORDER BY total_profit DESC
    """).fetchall()
    return [dict(r) for r in rows]


def get_trade_frequency(days: int = 30) -> List[Dict]:
    conn = _get_conn()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT
            strftime('%Y-%m-%d', timestamp) AS date,
            COUNT(*) AS total,
            SUM(CASE WHEN status IN ('executed','simulated') THEN 1 ELSE 0 END) AS successful,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed
        FROM trades
        WHERE timestamp >= ?
        GROUP BY date
        ORDER BY date
    """, (since + "T00:00:00",)).fetchall()
    return [dict(r) for r in rows]


def get_win_rate_over_time(days: int = 30) -> List[Dict]:
    conn = _get_conn()
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    rows = conn.execute("""
        SELECT
            strftime('%Y-%m-%d', timestamp) AS date,
            COUNT(*) AS total,
            SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) AS wins,
            ROUND(
                CAST(SUM(CASE WHEN profit > 0 THEN 1 ELSE 0 END) AS REAL) /
                MAX(COUNT(*), 1) * 100, 1
            ) AS win_rate
        FROM trades
        WHERE timestamp >= ? AND status IN ('executed','simulated')
        GROUP BY date
        ORDER BY date
    """, (since + "T00:00:00",)).fetchall()
    return [dict(r) for r in rows]


def get_overview() -> Dict[str, Any]:
    conn = _get_conn()
    row = conn.execute("""
        SELECT
            COUNT(*) AS total_trades,
            SUM(CASE WHEN status IN ('executed','simulated') THEN 1 ELSE 0 END) AS successful,
            SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) AS failed,
            COALESCE(SUM(profit), 0) AS total_profit,
            COALESCE(AVG(profit), 0) AS avg_profit,
            COALESCE(MAX(profit), 0) AS best_trade,
            COALESCE(MIN(profit), 0) AS worst_trade,
            COALESCE(AVG(total_cost), 0) AS avg_cost,
            COUNT(DISTINCT market_slug) AS unique_markets
        FROM trades
    """).fetchone()

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    today_row = conn.execute("""
        SELECT
            COUNT(*) AS trades,
            COALESCE(SUM(profit), 0) AS profit
        FROM trades
        WHERE timestamp >= ?
    """, (today + "T00:00:00",)).fetchone()

    merge_row = conn.execute("""
        SELECT
            COUNT(*) AS total_merges,
            COALESCE(SUM(usdc_received), 0) AS total_usdc
        FROM merges
        WHERE status IN ('success','simulated')
    """).fetchone()

    win_count = conn.execute("""
        SELECT COUNT(*) AS c FROM trades
        WHERE profit > 0 AND status IN ('executed','simulated')
    """).fetchone()["c"]
    total_valid = row["successful"] or 1

    return {
        "total_trades": row["total_trades"],
        "successful": row["successful"],
        "failed": row["failed"],
        "total_profit": round(row["total_profit"], 4),
        "avg_profit": round(row["avg_profit"], 4),
        "best_trade": round(row["best_trade"], 4),
        "worst_trade": round(row["worst_trade"], 4),
        "avg_cost": round(row["avg_cost"], 4),
        "unique_markets": row["unique_markets"],
        "win_rate": round(win_count / total_valid * 100, 1),
        "today_trades": today_row["trades"],
        "today_profit": round(today_row["profit"], 4),
        "total_merges": merge_row["total_merges"],
        "total_merge_usdc": round(merge_row["total_usdc"], 2),
    }
