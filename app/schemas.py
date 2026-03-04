from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class ObservationListCreate(BaseModel):
    title: str
    description: Optional[str] = None
    inat_user_id: Optional[int] = None
    inat_username: Optional[str] = None
    place_query: Optional[str] = None
    inat_dna_field_id: Optional[str] = None


class ObservationOut(BaseModel):
    taxon_name: str
    observed_at: Optional[datetime]
    inat_url: str
    dna_field_value: Optional[str]

    class Config:
        from_attributes = True
