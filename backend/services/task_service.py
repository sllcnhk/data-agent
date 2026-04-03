"""
任务服务

提供任务的CRUD操作和业务逻辑
"""
from typing import List, Optional, Dict, Any
from sqlalchemy.orm import Session
from sqlalchemy import desc, asc, and_
from sqlalchemy.exc import SQLAlchemyError
from uuid import UUID
from datetime import datetime

from backend.models.task import Task, TaskHistory, TaskType, TaskStatus


class TaskService:
    """任务服务"""

    def __init__(self, db: Session):
        """
        初始化任务服务

        Args:
            db: 数据库会话
        """
        self.db = db

    def create_task(
        self,
        task_type: TaskType,
        name: str,
        description: Optional[str] = None,
        conversation_id: Optional[str] = None,
        priority: int = 0,
        config: Optional[Dict[str, Any]] = None,
        input_data: Optional[Dict[str, Any]] = None,
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None
    ) -> Task:
        """
        创建新任务

        Args:
            task_type: 任务类型
            name: 任务名称
            description: 任务描述
            conversation_id: 对话ID
            priority: 优先级
            config: 任务配置
            input_data: 输入数据
            metadata: 元数据
            tags: 标签

        Returns:
            创建的任务对象

        Raises:
            SQLAlchemyError: 数据库错误
        """
        task = Task(
            task_type=task_type,
            name=name,
            description=description,
            conversation_id=conversation_id,
            priority=priority,
            config=config,
            input_data=input_data,
            metadata=metadata,
            tags=tags
        )

        try:
            self.db.add(task)
            self.db.commit()
            self.db.refresh(task)

            # 记录历史
            self._create_history_event(
                task_id=str(task.id),
                event_type="created",
                message=f"任务 '{name}' 已创建",
                new_status=task.status.value
            )

            return task
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def get_task(self, task_id: str) -> Optional[Task]:
        """
        获取任务

        Args:
            task_id: 任务ID

        Returns:
            任务对象或None
        """
        try:
            uuid_obj = UUID(task_id)
            return self.db.query(Task).filter(
                Task.id == uuid_obj
            ).first()
        except (ValueError, SQLAlchemyError):
            return None

    def list_tasks(
        self,
        limit: int = 20,
        offset: int = 0,
        status: Optional[TaskStatus] = None,
        task_type: Optional[TaskType] = None,
        conversation_id: Optional[str] = None,
        order_by: str = "created_at"
    ) -> List[Task]:
        """
        获取任务列表

        Args:
            limit: 限制数量
            offset: 偏移量
            status: 状态过滤
            task_type: 类型过滤
            conversation_id: 对话ID过滤
            order_by: 排序字段

        Returns:
            任务列表
        """
        query = self.db.query(Task)

        # 应用过滤条件
        if status:
            query = query.filter(Task.status == status)

        if task_type:
            query = query.filter(Task.task_type == task_type)

        if conversation_id:
            try:
                uuid_obj = UUID(conversation_id)
                query = query.filter(Task.conversation_id == uuid_obj)
            except ValueError:
                pass

        # 排序
        if order_by == "created_at":
            query = query.order_by(desc(Task.created_at))
        elif order_by == "updated_at":
            query = query.order_by(desc(Task.updated_at))
        elif order_by == "priority":
            query = query.order_by(desc(Task.priority))
        elif order_by == "name":
            query = query.order_by(asc(Task.name))

        return query.offset(offset).limit(limit).all()

    def update_task(
        self,
        task_id: str,
        **kwargs
    ) -> Optional[Task]:
        """
        更新任务

        Args:
            task_id: 任务ID
            **kwargs: 要更新的字段

        Returns:
            更新后的任务对象
        """
        task = self.get_task(task_id)
        if not task:
            return None

        try:
            old_status = task.status
            old_data = {k: v for k, v in vars(task).items() if not k.startswith('_')}

            for key, value in kwargs.items():
                if hasattr(task, key):
                    setattr(task, key, value)

            self.db.commit()
            self.db.refresh(task)

            # 如果状态发生变化,记录历史
            if 'status' in kwargs and old_status != task.status:
                self._create_history_event(
                    task_id=task_id,
                    event_type="status_changed",
                    message=f"任务状态从 {old_status.value} 变更为 {task.status.value}",
                    old_status=old_status.value,
                    new_status=task.status.value
                )

            return task
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def delete_task(self, task_id: str) -> bool:
        """
        删除任务

        Args:
            task_id: 任务ID

        Returns:
            是否删除成功
        """
        task = self.get_task(task_id)
        if not task:
            return False

        try:
            self.db.delete(task)
            self.db.commit()
            return True
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def start_task(self, task_id: str) -> Optional[Task]:
        """
        开始执行任务

        Args:
            task_id: 任务ID

        Returns:
            更新后的任务对象
        """
        task = self.get_task(task_id)
        if not task or task.status != TaskStatus.PENDING:
            return None

        try:
            task.start()
            self.db.commit()
            self.db.refresh(task)

            # 记录历史
            self._create_history_event(
                task_id=task_id,
                event_type="started",
                message=f"任务已开始执行",
                new_status=task.status.value
            )

            return task
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def complete_task(
        self,
        task_id: str,
        result: Optional[Dict[str, Any]] = None,
        output_files: Optional[List[str]] = None
    ) -> Optional[Task]:
        """
        标记任务完成

        Args:
            task_id: 任务ID
            result: 执行结果
            output_files: 输出文件列表

        Returns:
            更新后的任务对象
        """
        task = self.get_task(task_id)
        if not task or task.status != TaskStatus.RUNNING:
            return None

        try:
            task.complete(result=result, output_files=output_files)
            self.db.commit()
            self.db.refresh(task)

            # 记录历史
            self._create_history_event(
                task_id=task_id,
                event_type="completed",
                message=f"任务已完成",
                new_status=task.status.value
            )

            return task
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def fail_task(
        self,
        task_id: str,
        error_message: str,
        error_trace: Optional[str] = None
    ) -> Optional[Task]:
        """
        标记任务失败

        Args:
            task_id: 任务ID
            error_message: 错误信息
            error_trace: 错误堆栈

        Returns:
            更新后的任务对象
        """
        task = self.get_task(task_id)
        if not task or task.status not in [TaskStatus.PENDING, TaskStatus.RUNNING]:
            return None

        try:
            task.fail(error_message=error_message, error_trace=error_trace)
            self.db.commit()
            self.db.refresh(task)

            # 记录历史
            self._create_history_event(
                task_id=task_id,
                event_type="failed",
                message=f"任务失败: {error_message}",
                new_status=task.status.value
            )

            return task
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def update_progress(
        self,
        task_id: str,
        progress: int,
        current_step: Optional[str] = None
    ) -> Optional[Task]:
        """
        更新任务进度

        Args:
            task_id: 任务ID
            progress: 进度百分比(0-100)
            current_step: 当前步骤

        Returns:
            更新后的任务对象
        """
        task = self.get_task(task_id)
        if not task:
            return None

        try:
            old_progress = task.progress
            task.update_progress(progress=progress, current_step=current_step)
            self.db.commit()
            self.db.refresh(task)

            # 每10%进度记录一次历史
            if progress - old_progress >= 10 or progress == 100:
                self._create_history_event(
                    task_id=task_id,
                    event_type="progress",
                    message=f"任务进度更新: {progress}%",
                    event_data={
                        "progress": progress,
                        "current_step": current_step
                    }
                )

            return task
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def cancel_task(self, task_id: str) -> Optional[Task]:
        """
        取消任务

        Args:
            task_id: 任务ID

        Returns:
            更新后的任务对象
        """
        task = self.get_task(task_id)
        if not task or task.status in [TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED]:
            return None

        try:
            task.status = TaskStatus.CANCELLED
            task.completed_at = datetime.utcnow()

            if task.started_at:
                task.execution_time = int((task.completed_at - task.started_at).total_seconds())

            self.db.commit()
            self.db.refresh(task)

            # 记录历史
            self._create_history_event(
                task_id=task_id,
                event_type="cancelled",
                message=f"任务已取消",
                new_status=task.status.value
            )

            return task
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def pause_task(self, task_id: str) -> Optional[Task]:
        """
        暂停任务

        Args:
            task_id: 任务ID

        Returns:
            更新后的任务对象
        """
        task = self.get_task(task_id)
        if not task or task.status != TaskStatus.RUNNING:
            return None

        try:
            task.status = TaskStatus.PAUSED
            self.db.commit()
            self.db.refresh(task)

            # 记录历史
            self._create_history_event(
                task_id=task_id,
                event_type="paused",
                message=f"任务已暂停",
                new_status=task.status.value
            )

            return task
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def resume_task(self, task_id: str) -> Optional[Task]:
        """
        恢复任务

        Args:
            task_id: 任务ID

        Returns:
            更新后的任务对象
        """
        task = self.get_task(task_id)
        if not task or task.status != TaskStatus.PAUSED:
            return None

        try:
            task.status = TaskStatus.RUNNING
            self.db.commit()
            self.db.refresh(task)

            # 记录历史
            self._create_history_event(
                task_id=task_id,
                event_type="resumed",
                message=f"任务已恢复",
                new_status=task.status.value
            )

            return task
        except SQLAlchemyError as e:
            self.db.rollback()
            raise

    def get_task_history(
        self,
        task_id: str,
        limit: int = 50,
        offset: int = 0
    ) -> List[TaskHistory]:
        """
        获取任务历史

        Args:
            task_id: 任务ID
            limit: 限制数量
            offset: 偏移量

        Returns:
            任务历史列表
        """
        try:
            uuid_obj = UUID(task_id)
            return self.db.query(TaskHistory).filter(
                TaskHistory.task_id == uuid_obj
            ).order_by(desc(TaskHistory.created_at)).offset(offset).limit(limit).all()
        except (ValueError, SQLAlchemyError):
            return []

    def get_running_tasks(self) -> List[Task]:
        """
        获取正在运行的任务

        Returns:
            运行中的任务列表
        """
        return self.db.query(Task).filter(
            Task.status == TaskStatus.RUNNING
        ).order_by(desc(Task.priority)).all()

    def get_tasks_by_conversation(
        self,
        conversation_id: str,
        status: Optional[TaskStatus] = None
    ) -> List[Task]:
        """
        获取对话相关的任务

        Args:
            conversation_id: 对话ID
            status: 状态过滤

        Returns:
            任务列表
        """
        try:
            uuid_obj = UUID(conversation_id)
            query = self.db.query(Task).filter(
                Task.conversation_id == uuid_obj
            )

            if status:
                query = query.filter(Task.status == status)

            return query.order_by(desc(Task.created_at)).all()
        except (ValueError, SQLAlchemyError):
            return []

    def get_task_stats(
        self,
        conversation_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        获取任务统计信息

        Args:
            conversation_id: 对话ID(可选,用于过滤)

        Returns:
            统计信息字典
        """
        query = self.db.query(Task)

        if conversation_id:
            try:
                uuid_obj = UUID(conversation_id)
                query = query.filter(Task.conversation_id == uuid_obj)
            except ValueError:
                pass

        total_tasks = query.count()
        running_tasks = query.filter(Task.status == TaskStatus.RUNNING).count()
        completed_tasks = query.filter(Task.status == TaskStatus.COMPLETED).count()
        failed_tasks = query.filter(Task.status == TaskStatus.FAILED).count()
        pending_tasks = query.filter(Task.status == TaskStatus.PENDING).count()

        return {
            "total": total_tasks,
            "running": running_tasks,
            "completed": completed_tasks,
            "failed": failed_tasks,
            "pending": pending_tasks,
            "success_rate": (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
        }

    def _create_history_event(
        self,
        task_id: str,
        event_type: str,
        message: str,
        old_status: Optional[str] = None,
        new_status: Optional[str] = None,
        event_data: Optional[Dict[str, Any]] = None
    ) -> Optional[TaskHistory]:
        """
        创建历史事件

        Args:
            task_id: 任务ID
            event_type: 事件类型
            message: 事件消息
            old_status: 旧状态
            new_status: 新状态
            event_data: 事件数据

        Returns:
            创建的历史事件对象
        """
        try:
            uuid_obj = UUID(task_id)
            history = TaskHistory(
                task_id=uuid_obj,
                event_type=event_type,
                message=message,
                old_status=old_status,
                new_status=new_status,
                event_data=event_data
            )

            self.db.add(history)
            return history
        except (ValueError, SQLAlchemyError):
            return None
