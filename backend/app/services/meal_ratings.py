"""Anonymous browser-identity votes; ratings never change nutrition review flags."""
import hashlib
import uuid
from sqlalchemy import select, func
from sqlalchemy.dialects.postgresql import insert
from app.models import MealRating, NutritionCatalog

async def rating_summaries(session, item_ids):
    ids = [uuid.UUID(str(value)) for value in item_ids]
    if not ids: return {}
    rows = (await session.execute(select(MealRating.item_id, func.avg(MealRating.score).label("average"), func.count().label("votes"), func.max(MealRating.updated_at).label("updated_at")).where(MealRating.item_id.in_(ids)).group_by(MealRating.item_id))).all()
    return {str(row.item_id): {"rating_average": round(float(row.average), 2), "rating_count": row.votes, "rating_updated_at": row.updated_at,
                              "community_pick": row.votes >= 3 and float(row.average) >= 4} for row in rows}

async def attach_ratings(session, items):
    summaries = await rating_summaries(session, [item.item_id for item in items])
    for item in items:
        for key, value in summaries.get(str(item.item_id), {}).items(): setattr(item, key, value)
    return items

async def save_rating(session, item_id, voter_id, score):
    active = (await session.execute(select(NutritionCatalog.id).where(NutritionCatalog.id == item_id, NutritionCatalog.is_active.is_(True)))).scalar_one_or_none()
    if active is None: return None
    voter_hash = hashlib.sha256(str(voter_id).encode()).hexdigest()
    stmt = insert(MealRating).values(item_id=item_id, voter_hash=voter_hash, score=score)
    await session.execute(stmt.on_conflict_do_update(index_elements=[MealRating.item_id, MealRating.voter_hash], set_={"score":score,"updated_at":func.now()}))
    await session.commit()
    return (await rating_summaries(session,[item_id]))[str(item_id)]
