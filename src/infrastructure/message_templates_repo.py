from sqlalchemy import ForeignKey, Integer, String, Text, UniqueConstraint, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from src.domain.message_template import MessageTemplate
from src.infrastructure.db import Base


class MessageTemplateORM(Base):
    __tablename__ = "message_templates"
    # One override per (specialist, key): upsert replaces, absence means "use default".
    __table_args__ = (
        UniqueConstraint(
            "specialist_id", "template_key", name="uq_message_template_key"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    specialist_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("specialists.id"), nullable=False
    )
    template_key: Mapped[str] = mapped_column(String(64), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<MessageTemplateORM specialist_id={self.specialist_id} "
            f"key={self.template_key}>"
        )


class SqlAlchemyMessageTemplatesRepo:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _find(self, specialist_id: int, key: str) -> MessageTemplateORM | None:
        stmt = select(MessageTemplateORM).where(
            MessageTemplateORM.specialist_id == specialist_id,
            MessageTemplateORM.template_key == key,
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def get(self, specialist_id: int, key: str) -> MessageTemplate | None:
        orm = await self._find(specialist_id, key)
        if orm is None:
            return None
        return MessageTemplate(
            specialist_id=orm.specialist_id,
            template_key=orm.template_key,
            body=orm.body,
        )

    async def upsert(self, specialist_id: int, key: str, body: str) -> None:
        orm = await self._find(specialist_id, key)
        if orm is None:
            self._session.add(
                MessageTemplateORM(
                    specialist_id=specialist_id, template_key=key, body=body
                )
            )
        else:
            orm.body = body
        await self._session.commit()

    async def delete(self, specialist_id: int, key: str) -> bool:
        orm = await self._find(specialist_id, key)
        if orm is None:
            return False
        await self._session.delete(orm)
        await self._session.commit()
        return True
