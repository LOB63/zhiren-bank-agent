"""Explicit synthetic questionnaire answers for tests only, never autofill the user form."""
def assessment_fixture(**overrides):
    return {'age_band':'adult','income_source':'stable','emergency':'over6','debt':'none','goal':'growth',
            'knowledge':'advanced','reaction':'review','loss_capacity':'large','monthly_essential_fen':300000,**overrides}
