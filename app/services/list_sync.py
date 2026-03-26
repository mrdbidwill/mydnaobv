from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app import models
from app.services.inat import fetch_observations_for_list


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def sync_list_observations(db: Session, obs_list: models.ObservationList) -> int:
    """
    Sync iNaturalist observations into local cache for a list.
    Returns number of observations seen from iNaturalist iterator.
    """
    synced_count = 0
    observations = fetch_observations_for_list(obs_list)
    for obs in observations:
        synced_count += 1
        existing = (
            db.query(models.Observation)
            .filter_by(list_id=obs_list.id, inat_observation_id=obs.inat_id)
            .first()
        )
        if existing:
            existing.taxon_name = obs.taxon_name
            existing.species_guess = obs.species_guess
            existing.scientific_name = obs.scientific_name
            existing.common_name = obs.common_name
            existing.observation_taxon_id = obs.observation_taxon_id
            existing.observation_taxon_name = obs.observation_taxon_name
            existing.observation_taxon_rank = obs.observation_taxon_rank
            existing.community_taxon_id = obs.community_taxon_id
            existing.community_taxon_name = obs.community_taxon_name
            existing.community_taxon_rank = obs.community_taxon_rank
            existing.user_name = obs.user_name
            existing.observed_at = obs.observed_at
            existing.inat_url = obs.inat_url
            existing.dna_field_value = obs.dna_field_value
            existing.photo_url = obs.photo_url
            existing.photo_license_code = obs.photo_license_code
            existing.photo_attribution = obs.photo_attribution
            record = existing
        else:
            record = models.Observation(
                inat_observation_id=obs.inat_id,
                taxon_name=obs.taxon_name,
                species_guess=obs.species_guess,
                scientific_name=obs.scientific_name,
                common_name=obs.common_name,
                observation_taxon_id=obs.observation_taxon_id,
                observation_taxon_name=obs.observation_taxon_name,
                observation_taxon_rank=obs.observation_taxon_rank,
                community_taxon_id=obs.community_taxon_id,
                community_taxon_name=obs.community_taxon_name,
                community_taxon_rank=obs.community_taxon_rank,
                user_name=obs.user_name,
                observed_at=obs.observed_at,
                inat_url=obs.inat_url,
                dna_field_value=obs.dna_field_value,
                photo_url=obs.photo_url,
                photo_license_code=obs.photo_license_code,
                photo_attribution=obs.photo_attribution,
                list_id=obs_list.id,
            )
            db.add(record)
            db.flush()

        db.query(models.ObservationPhoto).filter_by(observation_id=record.id).delete(
            synchronize_session=False
        )
        for photo in obs.photo_entries:
            db.add(
                models.ObservationPhoto(
                    observation_id=record.id,
                    inat_photo_id=photo.inat_photo_id,
                    photo_index=photo.photo_index,
                    photo_url=photo.photo_url,
                    photo_license_code=photo.photo_license_code,
                    photo_attribution=photo.photo_attribution,
                )
            )

    obs_list.last_sync_at = utc_now_naive()
    db.commit()
    return synced_count
