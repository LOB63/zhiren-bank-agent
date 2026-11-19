import pytest
from scripts.verify_scope_style import unsupported_periodic_claim


@pytest.mark.parametrize('reply,unsupported',[
 ('金额一致，所以这是固定套餐。',True),
 ('可以改用现金支付。',True),
 ('仅凭记录无法判断是否为固定套餐或自动扣款。',False),
 ('不能判断是否为固定套餐，但肯定是自动扣款。',True),
])
def test_periodic_claim_judging_distinguishes_uncertainty_from_invention(reply,unsupported):
 assert unsupported_periodic_claim(reply) is unsupported
