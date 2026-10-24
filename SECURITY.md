# Política de Segurança

## Versões suportadas

| Versão | Suporte          |
| ------ | ---------------- |
| 0.1.x  | desenvolvimento ativo (Cycle 1) |

O projeto está em fase inicial; apenas a linha mais recente recebe correções.

## Reportando vulnerabilidades

**Não abra issue pública para vulnerabilidades.** Reporte diretamente ao
mantenedor pelo GitHub (perfil `@EdgarSocrates98`) com:

- descrição do problema e impacto potencial;
- passos de reprodução ou prova de conceito;
- versão/commit afetado e ambiente (SO, Python).

Você receberá uma confirmação em até 7 dias. Se a vulnerabilidade for aceita,
a correção e o crédito serão coordenados antes da divulgação.

## Modelo de ameaças resumido

O Code Atlas trata o **repositório indexado como não confiável**:

- nenhum código do repositório varrido é executado, importado ou instalado;
- nenhum dado sai da máquina por padrão (sem telemetria, sem egress);
- segredos são excluídos de inventário, logs e grafos;
- proteção contra path traversal, fuga por symlink, arquivos gigantes e
  entradas malformadas.

O documento completo está em `docs/threat-model.md` (spec lf-0005).
