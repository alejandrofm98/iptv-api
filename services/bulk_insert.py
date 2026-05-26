"""
Módulo optimizado para inserciones masivas en PostgreSQL
Usa SQLAlchemy core engine directamente.
"""
import time
from typing import List, Dict, Any, Optional, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

from sqlalchemy import text
from app.database import engine as default_engine


@dataclass
class InsertStats:
    total_records: int = 0
    inserted_records: int = 0
    failed_records: int = 0
    start_time: float = 0
    batches_completed: int = 0

    def __post_init__(self):
        self.start_time = time.time()

    def get_progress_pct(self) -> float:
        if self.total_records == 0:
            return 0.0
        return (self.inserted_records / self.total_records) * 100

    def get_elapsed_time(self) -> float:
        return time.time() - self.start_time

    def get_rate(self) -> float:
        elapsed = self.get_elapsed_time()
        if elapsed == 0:
            return 0
        return self.inserted_records / elapsed

    def get_eta(self) -> float:
        rate = self.get_rate()
        if rate == 0:
            return 0
        remaining = self.total_records - self.inserted_records
        return remaining / rate

    def format_time(self, seconds: float) -> str:
        if seconds < 60:
            return f"{seconds:.0f}s"
        elif seconds < 3600:
            return f"{seconds/60:.1f}min"
        else:
            return f"{seconds/3600:.1f}h"


def _bulk_insert(engine, table_name: str, columns: List[str], rows: Sequence[tuple]) -> int:
    col_names = ", ".join(columns)
    placeholders = ", ".join([f":{c}" for c in columns])
    sql = f"INSERT INTO {table_name} ({col_names}) VALUES ({placeholders})"
    params_list = [dict(zip(columns, row)) for row in rows]
    with engine.begin() as conn:
        conn.execute(text(sql), params_list)
    return len(rows)


class BulkInserter:
    def __init__(
        self,
        table_name: str,
        columns: List[str],
        batch_size: int = 1000,
        max_workers: int = 4,
        progress_callback: Optional[Callable[[InsertStats], None]] = None,
        engine=None,
    ):
        self.engine = engine or default_engine
        self.table_name = table_name
        self.columns = columns
        self.batch_size = batch_size
        self.max_workers = max_workers
        self.progress_callback = progress_callback
        self.stats = InsertStats()
        self._lock_lock = None

    def _create_batches(self, data: List[Dict[str, Any]]) -> List[List[tuple]]:
        batches = []
        for i in range(0, len(data), self.batch_size):
            batch = data[i:i + self.batch_size]
            batches.append(batch)
        return batches

    def _insert_batch(
        self,
        batch: List[Dict[str, Any]],
        batch_num: int,
        total_batches: int
    ) -> tuple[bool, int]:
        for attempt in range(3):
            try:
                rows = [tuple(self._row_to_tuple(item)) for item in batch]
                inserted = _bulk_insert(self.engine, self.table_name, self.columns, rows)
                with self._lock():
                    self.stats.inserted_records += inserted
                    self.stats.batches_completed += 1
                    if self.progress_callback:
                        self.progress_callback(self.stats)
                time.sleep(0.05 if len(batch) < 500 else 0.1)
                return True, inserted
            except Exception as e:
                if attempt < 2:
                    wait_time = (attempt + 1) * 2
                    print(f"Batch {batch_num}/{total_batches} fallo (intento {attempt + 1}/3), reintentando en {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    print(f"Batch {batch_num}/{total_batches} fallo tras 3 intentos: {e}")
                    with self._lock():
                        self.stats.failed_records += len(batch)
                    return False, 0
        return False, 0

    def _row_to_tuple(self, row: Dict[str, Any]) -> tuple:
        return tuple(row.get(col) for col in self.columns)

    def _lock(self):
        return self._lock_lock

    def insert_bulk(self, data: List[Dict[str, Any]]) -> InsertStats:
        if not data:
            print("No hay datos para insertar")
            return self.stats

        self.stats = InsertStats()
        self.stats.total_records = len(data)

        print(f"Iniciando insercion masiva en tabla '{self.table_name}':")
        print(f"   Registros: {len(data):,}")
        print(f"   Batch size: {self.batch_size:,}")
        print(f"   Workers: {self.max_workers}")

        batches = self._create_batches(data)
        total_batches = len(batches)
        print(f"   Total batches: {total_batches}")
        print()

        import threading
        self._lock_lock = threading.Lock()

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {
                executor.submit(self._insert_batch, batch, i + 1, total_batches): i
                for i, batch in enumerate(batches)
            }
            for future in as_completed(futures):
                batch_idx = futures[future]
                try:
                    success, records = future.result()
                except Exception as e:
                    print(f"Error inesperado en batch {batch_idx + 1}: {e}")

        self._print_summary()
        return self.stats

    def _print_summary(self):
        elapsed = self.stats.get_elapsed_time()
        rate = self.stats.get_rate()
        print(f"\n{'='*60}")
        print(f"Insercion completada en tabla '{self.table_name}'")
        print(f"{'='*60}")
        print(f"Total:     {self.stats.total_records:,}")
        print(f"OK:        {self.stats.inserted_records:,} ({self.stats.get_progress_pct():.1f}%)")
        print(f"Fallidos:  {self.stats.failed_records:,}")
        print(f"Tiempo:    {self.stats.format_time(elapsed)}")
        print(f"Velocidad: {rate:.0f} reg/s")
        print(f"{'='*60}\n")


def insert_bulk_optimized(
    table_name: str = None,
    columns: List[str] = None,
    data: List[Dict[str, Any]] = None,
    batch_size: int = 1000,
    max_workers: int = 4,
    engine=None,
    pool=None,
    **kwargs,
) -> InsertStats:
    engine = engine or pool or default_engine
    cols = columns or kwargs.pop("columns", None)
    tbl = table_name or kwargs.pop("table_name", None)
    dt = data or kwargs.pop("data", None)
    if not tbl or not dt:
        raise ValueError("table_name and data are required")
    inserter = BulkInserter(
        table_name=tbl,
        columns=cols or list(dt[0].keys()) if dt else [],
        batch_size=batch_size,
        max_workers=max_workers,
        progress_callback=default_progress_callback,
        engine=engine,
    )
    return inserter.insert_bulk(dt)


def default_progress_callback(stats: InsertStats):
    if stats.batches_completed % 5 == 0:
        progress_pct = stats.get_progress_pct()
        rate = stats.get_rate()
        eta = stats.get_eta()
        print(
            f"      {stats.inserted_records:,}/{stats.total_records:,} "
            f"({progress_pct:.1f}%) | "
            f"{rate:.0f} reg/s | "
            f"ETA: {stats.format_time(eta)}"
        )
