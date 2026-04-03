"""
Auto-Vectorization Manager - Phase 3.4

自动向量化管理器 - 后台自动处理消息向量化

功能:
- 后台异步向量化
- 增量索引（只处理新消息）
- 批量处理优化
- 错误重试机制
- 进度跟踪
"""
import logging
import threading
import queue
import time
from typing import List, Optional, Dict, Any, Callable
from datetime import datetime
from dataclasses import dataclass
from enum import Enum

from backend.core.conversation_format import UnifiedMessage, MessageRole
from backend.core.vector_store import VectorStoreManager
from backend.core.embedding_service import EmbeddingService

logger = logging.getLogger(__name__)


class VectorizationStatus(str, Enum):
    """向量化状态"""
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass
class VectorizationTask:
    """向量化任务"""
    conversation_id: str
    message_id: str
    content: str
    role: str
    priority: int = 0
    created_at: datetime = None
    retry_count: int = 0
    status: VectorizationStatus = VectorizationStatus.PENDING
    error: Optional[str] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()

    def __lt__(self, other):
        """优先级排序（priority 越高越优先）"""
        return self.priority > other.priority


class AutoVectorizationManager:
    """自动向量化管理器"""

    def __init__(
        self,
        vector_store: Optional[VectorStoreManager] = None,
        embedding_service: Optional[EmbeddingService] = None,
        batch_size: int = 10,
        max_queue_size: int = 1000,
        worker_threads: int = 2,
        retry_limit: int = 3,
        enable_auto_start: bool = True
    ):
        """
        初始化自动向量化管理器

        Args:
            vector_store: 向量存储管理器（可选，默认创建新实例）
            embedding_service: Embedding 服务（可选，默认创建 mock）
            batch_size: 批处理大小
            max_queue_size: 最大队列大小
            worker_threads: 工作线程数
            retry_limit: 最大重试次数
            enable_auto_start: 是否自动启动
        """
        self.vector_store = vector_store or VectorStoreManager()
        self.embedding_service = embedding_service or EmbeddingService(provider="mock")

        self.batch_size = batch_size
        self.max_queue_size = max_queue_size
        self.worker_threads = worker_threads
        self.retry_limit = retry_limit

        # 任务队列（优先级队列）
        self.task_queue = queue.PriorityQueue(maxsize=max_queue_size)

        # 工作线程
        self.workers = []
        self.running = False
        self.lock = threading.Lock()

        # 统计信息
        self.stats = {
            "total_submitted": 0,
            "total_processed": 0,
            "total_failed": 0,
            "total_skipped": 0,
            "current_queue_size": 0,
            "last_processed_time": None
        }

        # 已处理的消息集合（避免重复）
        self.processed_messages = set()

        # 回调函数
        self.on_success_callback: Optional[Callable] = None
        self.on_failure_callback: Optional[Callable] = None

        if enable_auto_start:
            self.start()

        logger.info(
            f"AutoVectorizationManager initialized: "
            f"batch_size={batch_size}, workers={worker_threads}"
        )

    def start(self):
        """启动工作线程"""
        if self.running:
            logger.warning("AutoVectorizationManager is already running")
            return

        self.running = True

        # 创建并启动工作线程
        for i in range(self.worker_threads):
            worker = threading.Thread(
                target=self._worker_loop,
                name=f"VectorizationWorker-{i}",
                daemon=True
            )
            worker.start()
            self.workers.append(worker)

        logger.info(f"AutoVectorizationManager started with {self.worker_threads} workers")

    def stop(self, wait: bool = True, timeout: float = 10.0):
        """
        停止工作线程

        Args:
            wait: 是否等待队列清空
            timeout: 等待超时时间（秒）
        """
        if not self.running:
            logger.warning("AutoVectorizationManager is not running")
            return

        logger.info("Stopping AutoVectorizationManager...")

        if wait:
            # 等待队列清空
            start_time = time.time()
            while not self.task_queue.empty():
                if time.time() - start_time > timeout:
                    logger.warning(f"Timeout waiting for queue to clear ({self.task_queue.qsize()} tasks remaining)")
                    break
                time.sleep(0.1)

        self.running = False

        # 等待工作线程结束
        for worker in self.workers:
            worker.join(timeout=2.0)

        self.workers.clear()

        logger.info("AutoVectorizationManager stopped")

    def submit_message(
        self,
        conversation_id: str,
        message: UnifiedMessage,
        message_id: Optional[str] = None,
        priority: int = 0
    ) -> bool:
        """
        提交消息进行向量化

        Args:
            conversation_id: 对话 ID
            message: 消息对象
            message_id: 消息 ID（可选，默认生成）
            priority: 优先级（越高越优先）

        Returns:
            是否成功提交
        """
        if not self.running:
            logger.error("AutoVectorizationManager is not running")
            return False

        # 跳过系统消息
        if message.role == MessageRole.SYSTEM:
            self.stats["total_skipped"] += 1
            return True

        # 生成消息 ID
        if message_id is None:
            message_id = f"msg_{int(time.time() * 1000)}_{id(message)}"

        # 检查是否已处理
        msg_key = f"{conversation_id}:{message_id}"
        if msg_key in self.processed_messages:
            logger.debug(f"Message already processed: {msg_key}")
            self.stats["total_skipped"] += 1
            return True

        # 创建任务
        task = VectorizationTask(
            conversation_id=conversation_id,
            message_id=message_id,
            content=message.content,
            role=str(message.role),
            priority=priority
        )

        # 提交到队列
        try:
            # 使用 priority 作为排序键
            self.task_queue.put((task.priority, task), block=False)

            with self.lock:
                self.stats["total_submitted"] += 1
                self.stats["current_queue_size"] = self.task_queue.qsize()

            logger.debug(f"Submitted task: {msg_key} (priority={priority})")
            return True

        except queue.Full:
            logger.error("Task queue is full, cannot submit new task")
            return False

    def submit_batch(
        self,
        conversation_id: str,
        messages: List[UnifiedMessage],
        priority: int = 0
    ) -> int:
        """
        批量提交消息

        Args:
            conversation_id: 对话 ID
            messages: 消息列表
            priority: 优先级

        Returns:
            成功提交的数量
        """
        count = 0
        for i, msg in enumerate(messages):
            if self.submit_message(
                conversation_id=conversation_id,
                message=msg,
                message_id=f"msg_{i:04d}",
                priority=priority
            ):
                count += 1

        logger.info(f"Batch submitted: {count}/{len(messages)} messages")
        return count

    def _worker_loop(self):
        """工作线程主循环"""
        logger.debug(f"Worker {threading.current_thread().name} started")

        while self.running:
            try:
                # 获取任务（超时避免阻塞）
                try:
                    priority, task = self.task_queue.get(timeout=1.0)
                except queue.Empty:
                    continue

                # 处理任务
                try:
                    task.status = VectorizationStatus.PROCESSING
                    self._process_task(task)
                    task.status = VectorizationStatus.COMPLETED

                    # 标记为已处理
                    msg_key = f"{task.conversation_id}:{task.message_id}"
                    self.processed_messages.add(msg_key)

                    # 更新统计
                    with self.lock:
                        self.stats["total_processed"] += 1
                        self.stats["current_queue_size"] = self.task_queue.qsize()
                        self.stats["last_processed_time"] = datetime.now()

                    # 回调
                    if self.on_success_callback:
                        try:
                            self.on_success_callback(task)
                        except Exception as e:
                            logger.error(f"Success callback error: {e}")

                except Exception as e:
                    task.status = VectorizationStatus.FAILED
                    task.error = str(e)
                    task.retry_count += 1

                    logger.error(
                        f"Task failed: {task.conversation_id}:{task.message_id} "
                        f"(retry={task.retry_count}/{self.retry_limit}): {e}"
                    )

                    # 重试
                    if task.retry_count < self.retry_limit:
                        logger.info(f"Retrying task: {task.conversation_id}:{task.message_id}")
                        time.sleep(1.0 * task.retry_count)  # 指数退避
                        self.task_queue.put((task.priority, task))
                    else:
                        # 超过重试次数
                        with self.lock:
                            self.stats["total_failed"] += 1

                        # 回调
                        if self.on_failure_callback:
                            try:
                                self.on_failure_callback(task)
                            except Exception as e2:
                                logger.error(f"Failure callback error: {e2}")

                finally:
                    self.task_queue.task_done()

            except Exception as e:
                logger.error(f"Worker loop error: {e}", exc_info=True)

        logger.debug(f"Worker {threading.current_thread().name} stopped")

    def _process_task(self, task: VectorizationTask):
        """
        处理单个任务

        Args:
            task: 向量化任务
        """
        # 添加到向量存储
        self.vector_store.add_message(
            message_id=task.message_id,
            content=task.content,
            conversation_id=task.conversation_id,
            role=task.role,
            metadata={
                "created_at": task.created_at.isoformat(),
                "priority": task.priority
            }
        )

        logger.debug(
            f"Processed: {task.conversation_id}:{task.message_id} "
            f"({len(task.content)} chars)"
        )

    def get_stats(self) -> Dict[str, Any]:
        """
        获取统计信息

        Returns:
            统计信息字典
        """
        with self.lock:
            return {
                **self.stats,
                "running": self.running,
                "worker_threads": len(self.workers),
                "processed_messages_count": len(self.processed_messages)
            }

    def clear_processed_cache(self):
        """清空已处理消息缓存"""
        with self.lock:
            self.processed_messages.clear()
            logger.info("Cleared processed messages cache")

    def set_callbacks(
        self,
        on_success: Optional[Callable] = None,
        on_failure: Optional[Callable] = None
    ):
        """
        设置回调函数

        Args:
            on_success: 成功回调
            on_failure: 失败回调
        """
        self.on_success_callback = on_success
        self.on_failure_callback = on_failure

    def wait_until_complete(self, timeout: Optional[float] = None):
        """
        等待队列完成

        Args:
            timeout: 超时时间（秒）
        """
        if timeout:
            start_time = time.time()
            while not self.task_queue.empty():
                if time.time() - start_time > timeout:
                    raise TimeoutError(f"Timeout waiting for queue completion")
                time.sleep(0.1)
        else:
            self.task_queue.join()

    def is_idle(self) -> bool:
        """检查是否空闲（队列为空）"""
        return self.task_queue.empty()

    def __enter__(self):
        """上下文管理器入口"""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口"""
        self.stop(wait=True)


# 全局单例
_auto_vectorization_manager = None


def get_auto_vectorization_manager(
    vector_store: Optional[VectorStoreManager] = None,
    embedding_service: Optional[EmbeddingService] = None,
    **kwargs
) -> AutoVectorizationManager:
    """
    获取自动向量化管理器单例

    Args:
        vector_store: 向量存储
        embedding_service: Embedding 服务
        **kwargs: 其他参数

    Returns:
        AutoVectorizationManager 实例
    """
    global _auto_vectorization_manager

    if _auto_vectorization_manager is None:
        _auto_vectorization_manager = AutoVectorizationManager(
            vector_store=vector_store,
            embedding_service=embedding_service,
            **kwargs
        )

    return _auto_vectorization_manager
