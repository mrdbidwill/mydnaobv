from sqlalchemy.orm import configure_mappers


def test_mapper_configuration_for_export_relationships():
    # Ensures relationship annotations can be resolved during runtime mapper setup.
    configure_mappers()
    assert True
