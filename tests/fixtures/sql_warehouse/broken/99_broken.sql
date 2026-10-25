-- Intencionalmente malformado: exercita extração parcial + resync.
SELECT FROM ;
SELECT recovered FROM staging.order_facts;
BROKEN SQL HERE;
SELECT tail_ok FROM landing.orders;
