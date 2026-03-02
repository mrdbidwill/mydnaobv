from datetime import datetime
from pydantic import BaseModel


class ObservationListCreate(BaseModel):
    title: str
    description: str | None = None
    inat_user_id: int
    inat_username: str | None = None
    inat_dna_field_id: str | None = None


class ObservationOut(BaseModel):
    taxon_name: str
    observed_at: datetime | None
    inat_url: str
    dna_field_value: str | None

    class Config:
        from_attributes = True
