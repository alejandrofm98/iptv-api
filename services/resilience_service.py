"""
Servicios de resiliencia para streams: Circuit Breaker, Retry y Buffering
"""
import asyncio
import random
import time
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, TypeVar
from urllib.parse import urlparse
import httpx

logger = logging.getLogger("resilience")
logger.setLevel(logging.DEBUG)

T = TypeVar('T')


class CircuitState(Enum):
    """Estados del Circuit Breaker"""
    CLOSED = "closed"      # Funcionamiento normal
    OPEN = "open"          # Circuito abierto, rechazando peticiones
    HALF_OPEN = "half_open"  # Probando si el servicio se recuperó


@dataclass
class CircuitBreakerConfig:
    """Configuración del Circuit Breaker"""
    failure_threshold: int = 5          # Fallos consecutivos para abrir circuito
    recovery_timeout: float = 30.0      # Segundos antes de intentar half-open
    half_open_max_calls: int = 3        # Máximo de llamadas en half-open
    success_threshold: int = 2          # Éxitos consecutivos para cerrar circuito


@dataclass
class CircuitBreakerMetrics:
    """Métricas del Circuit Breaker"""
    failures: int = 0
    successes: int = 0
    last_failure_time: Optional[float] = None
    state: CircuitState = CircuitState.CLOSED
    half_open_calls: int = 0


class CircuitBreakerService:
    """
    Circuit Breaker para proteger contra cascadas de fallos.
    
    Si un proveedor falla repetidamente, el circuito se abre
    y rechaza peticiones rápidamente durante un tiempo.
    """
    
    def __init__(self, config: Optional[CircuitBreakerConfig] = None):
        self.config = config or CircuitBreakerConfig()
        self._circuits: Dict[str, CircuitBreakerMetrics] = {}
        self._lock = asyncio.Lock()
    
    def _get_hostname(self, url: str) -> str:
        """Extrae el hostname de una URL"""
        try:
            return urlparse(url).hostname or "unknown"
        except:
            return "unknown"
    
    async def can_execute(self, url: str) -> bool:
        """Verifica si se puede ejecutar una petición"""
        hostname = self._get_hostname(url)
        
        async with self._lock:
            metrics = self._circuits.get(hostname)
            if not metrics:
                return True
            
            if metrics.state == CircuitState.OPEN:
                # Verificar si es tiempo de intentar recuperación
                if metrics.last_failure_time:
                    elapsed = time.time() - metrics.last_failure_time
                    if elapsed >= self.config.recovery_timeout:
                        metrics.state = CircuitState.HALF_OPEN
                        metrics.half_open_calls = 0
                        logger.info(f"🔓 Circuit HALF_OPEN: {hostname}")
                        return True
                return False
            
            if metrics.state == CircuitState.HALF_OPEN:
                return metrics.half_open_calls < self.config.half_open_max_calls
            
            return True
    
    async def record_success(self, url: str):
        """Registra un éxito"""
        hostname = self._get_hostname(url)
        
        async with self._lock:
            metrics = self._circuits.get(hostname)
            if not metrics:
                return
            
            metrics.successes += 1
            metrics.failures = 0
            
            if metrics.state == CircuitState.HALF_OPEN:
                metrics.half_open_calls += 1
                if metrics.successes >= self.config.success_threshold:
                    metrics.state = CircuitState.CLOSED
                    metrics.half_open_calls = 0
                    logger.info(f"✅ Circuit CLOSED: {hostname}")
    
    async def record_failure(self, url: str):
        """Registra un fallo"""
        hostname = self._get_hostname(url)
        
        async with self._lock:
            metrics = self._circuits.get(hostname)
            if not metrics:
                metrics = CircuitBreakerMetrics()
                self._circuits[hostname] = metrics
            
            metrics.failures += 1
            metrics.successes = 0
            metrics.last_failure_time = time.time()
            
            if metrics.state == CircuitState.HALF_OPEN:
                # Fallo en half-open, volver a OPEN
                metrics.state = CircuitState.OPEN
                logger.warning(f"❌ Circuit OPEN (half-open fail): {hostname}")
            elif metrics.failures >= self.config.failure_threshold:
                # Umbral de fallos alcanzado
                metrics.state = CircuitState.OPEN
                logger.warning(f"❌ Circuit OPEN ({metrics.failures} failures): {hostname}")
    
    def get_status(self, url: str) -> Dict[str, Any]:
        """Obtiene el estado del circuit breaker para una URL"""
        hostname = self._get_hostname(url)
        metrics = self._circuits.get(hostname)
        
        if not metrics:
            return {"state": "closed", "failures": 0, "successes": 0}
        
        return {
            "state": metrics.state.value,
            "failures": metrics.failures,
            "successes": metrics.successes,
            "last_failure": metrics.last_failure_time
        }
    
    def get_all_status(self) -> Dict[str, Dict[str, Any]]:
        """Obtiene el estado de todos los circuitos"""
        return {
            hostname: {
                "state": m.state.value,
                "failures": m.failures,
                "successes": m.successes
            }
            for hostname, m in self._circuits.items()
        }


@dataclass
class RetryConfig:
    """Configuración de retry con backoff"""
    max_attempts: int = 3               # Máximo de intentos
    initial_delay: float = 1.0          # Delay inicial en segundos
    max_delay: float = 30.0             # Delay máximo
    exponential_base: float = 2.0       # Base para backoff exponencial
    jitter: bool = True                 # Agregar jitter aleatorio
    retryable_exceptions: tuple = (     # Excepciones que ameritan retry
        httpx.TimeoutException,
        httpx.ConnectError,
        httpx.NetworkError,
        httpx.ReadError,
        httpx.WriteError,
        ConnectionError,
        OSError,
    )


class RetryService:
    """
    Servicio de reintentos con backoff exponencial.
    """
    
    def __init__(self, config: Optional[RetryConfig] = None):
        self.config = config or RetryConfig()
    
    def _calculate_delay(self, attempt: int) -> float:
        """Calcula el delay con backoff exponencial y jitter"""
        delay = min(
            self.config.initial_delay * (self.config.exponential_base ** attempt),
            self.config.max_delay
        )
        
        if self.config.jitter:
            # Agregar jitter: ±25% aleatorio
            jitter_factor = 0.75 + (random.random() * 0.5)
            delay *= jitter_factor
        
        return delay
    
    async def execute_with_retry(
        self,
        func: Callable[..., T],
        *args,
        **kwargs
    ) -> T:
        """
        Ejecuta una función con retry y backoff.
        
        Args:
            func: Función a ejecutar
            *args: Argumentos posicionales
            **kwargs: Argumentos nombrados
            
        Returns:
            Resultado de la función
            
        Raises:
            Exception: Si se agotan los reintentos
        """
        last_exception = None
        
        for attempt in range(self.config.max_attempts):
            try:
                if asyncio.iscoroutinefunction(func):
                    result = await func(*args, **kwargs)
                else:
                    result = func(*args, **kwargs)
                return result
                
            except self.config.retryable_exceptions as e:
                last_exception = e
                
                if attempt < self.config.max_attempts - 1:
                    delay = self._calculate_delay(attempt)
                    logger.warning(f"🔄 Retry {attempt + 1}/{self.config.max_attempts}: {e}. Waiting {delay:.1f}s")
                    await asyncio.sleep(delay)
                else:
                    logger.error(f"❌ Retry exhausted after {self.config.max_attempts} attempts")
                    raise last_exception
            except Exception:
                # Excepción no retryable, propagar inmediatamente
                raise
        
        raise last_exception or Exception("Retry failed")


@dataclass
class StreamBufferConfig:
    """Configuración del buffer de stream"""
    pre_buffer_seconds: float = 3.0     # Segundos de pre-buffer
    chunk_size: int = 8192              # Tamaño de chunk en bytes
    max_buffer_size: int = 10 * 1024 * 1024  # 10MB máximo
    min_buffer_chunks: int = 10         # Mínimo de chunks antes de empezar


class StreamBuffer:
    """
    Buffer de pre-carga para streams.
    
    Acumula datos antes de empezar a enviarlos al cliente,
    evitando cortes al inicio de la reproducción.
    """
    
    def __init__(self, config: Optional[StreamBufferConfig] = None):
        self.config = config or StreamBufferConfig()
        self._buffer: asyncio.Queue[Optional[bytes]] = asyncio.Queue()
        self._buffered_bytes: int = 0
        self._buffered_chunks: int = 0
        self._streaming_started: bool = False
        self._start_time: Optional[float] = None
    
    async def feed(self, chunk: bytes):
        """Alimenta el buffer con un chunk"""
        if self._buffered_bytes >= self.config.max_buffer_size:
            # Buffer lleno, empezar a streamear
            self._streaming_started = True
        
        await self._buffer.put(chunk)
        self._buffered_bytes += len(chunk)
        self._buffered_chunks += 1
    
    async def should_start_streaming(self) -> bool:
        """Determina si ya hay suficiente buffer para empezar"""
        if self._streaming_started:
            return True
        
        # Condiciones para empezar:
        # 1. Mínimo de chunks acumulados
        # 2. Tiempo de pre-buffer transcurrido
        min_chunks_met = self._buffered_chunks >= self.config.min_buffer_chunks
        
        if self._start_time is None and min_chunks_met:
            self._start_time = time.time()
        
        time_met = False
        if self._start_time:
            elapsed = time.time() - self._start_time
            time_met = elapsed >= self.config.pre_buffer_seconds
        
        if min_chunks_met and time_met:
            self._streaming_started = True
            logger.info(f"▶️ Buffer ready: {self._buffered_chunks} chunks ({self._buffered_bytes/1024:.1f}KB)")
            return True
        
        return False
    
    async def get_chunk(self) -> Optional[bytes]:
        """Obtiene el siguiente chunk del buffer"""
        try:
            # Esperar activamente hasta que haya datos o timeout
            return await asyncio.wait_for(
                self._buffer.get(),
                timeout=30.0
            )
        except asyncio.TimeoutError:
            return None
    
    def get_stats(self) -> Dict[str, Any]:
        """Obtiene estadísticas del buffer"""
        return {
            "buffered_bytes": self._buffered_bytes,
            "buffered_chunks": self._buffered_chunks,
            "streaming_started": self._streaming_started,
            "buffer_seconds": self.config.pre_buffer_seconds,
            "queue_size": self._buffer.qsize()
        }
    
    def mark_complete(self):
        """Marca el buffer como completo (no más datos)"""
        self._buffer.put_nowait(None)


class ResilienceService:
    """
    Servicio combinado de resiliencia: Circuit Breaker + Retry + Buffer
    """
    
    def __init__(
        self,
        circuit_config: Optional[CircuitBreakerConfig] = None,
        retry_config: Optional[RetryConfig] = None,
        buffer_config: Optional[StreamBufferConfig] = None
    ):
        self.circuit_breaker = CircuitBreakerService(circuit_config)
        self.retry_service = RetryService(retry_config)
        self.buffer_config = buffer_config or StreamBufferConfig()
    
    async def execute_with_resilience(
        self,
        url: str,
        func: Callable[..., T],
        *args,
        **kwargs
    ) -> T:
        """
        Ejecuta una función con circuit breaker y retry.
        
        Args:
            url: URL del recurso (para tracking del circuit breaker)
            func: Función a ejecutar
            *args, **kwargs: Argumentos de la función
            
        Returns:
            Resultado de la función
        """
        # Verificar circuit breaker
        if not await self.circuit_breaker.can_execute(url):
            raise Exception(f"Circuit breaker OPEN para {url}")
        
        try:
            # Ejecutar con retry
            result = await self.retry_service.execute_with_retry(func, *args, **kwargs)
            # Registrar éxito
            await self.circuit_breaker.record_success(url)
            return result
            
        except Exception as e:
            # Registrar fallo
            await self.circuit_breaker.record_failure(url)
            raise
    
    def create_buffer(self) -> StreamBuffer:
        """Crea un nuevo buffer de stream"""
        return StreamBuffer(self.buffer_config)
    
    def get_status(self) -> Dict[str, Any]:
        """Obtiene estado de todos los componentes"""
        return {
            "circuits": self.circuit_breaker.get_all_status(),
            "retry_config": {
                "max_attempts": self.retry_service.config.max_attempts,
                "initial_delay": self.retry_service.config.initial_delay
            },
            "buffer_config": {
                "pre_buffer_seconds": self.buffer_config.pre_buffer_seconds,
                "chunk_size": self.buffer_config.chunk_size
            }
        }
