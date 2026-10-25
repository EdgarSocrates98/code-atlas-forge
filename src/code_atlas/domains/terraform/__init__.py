"""Adapter de domínio Terraform (lf-0115, Cycle 7 Phase 7).

Extrai ``TERRAFORM_RESOURCE``/``TERRAFORM_MODULE``/``IAM_ROLE`` de
``.tf`` (HCL hand-rolled), ``.tf.json`` e ``.tfvars`` — estático puro:
nenhuma execução de ``terraform``, state, plan ou apply; nenhuma
conexão externa; segredos redigidos; ``${…}`` vira ``${dynamic}``;
``count``/``for_each`` declarados como ``dynamic``, sem expansão.
"""

from code_atlas.domains.terraform.adapter import (
    ADAPTER_VERSION,
    TerraformAdapter,
)
from code_atlas.domains.terraform.extract import DOMAIN_ID

__all__ = [
    "ADAPTER_VERSION",
    "DOMAIN_ID",
    "TerraformAdapter",
]
