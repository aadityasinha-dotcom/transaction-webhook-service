-- ============================================================================
-- 0002: processing lease
--
-- Run this if you applied 0001 before the processing_started_at column existed.
-- (Fresh installs get everything from 0001; this file is then a harmless no-op.)
--
-- The processing endpoint now *claims* a row (processing_started_at = now()) before doing
-- the slow work, so a duplicate delivery of the trigger request returns immediately instead
-- of sleeping 30 s. The retry job only re-sends rows whose claim is older than 2 minutes.
-- ============================================================================

alter table public.transactions add column if not exists processing_started_at timestamptz;

create or replace function public.retry_stuck_transactions()
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
    v_row   record;
    v_count integer := 0;
begin
    for v_row in
        select transaction_id
        from public.transactions
        where status = 'PROCESSING'
          and created_at < now() - interval '2 minutes'
          and (processing_started_at is null or processing_started_at < now() - interval '2 minutes')
          and processing_attempts < 20
        order by created_at
        limit 100
    loop
        perform public.request_transaction_processing(v_row.transaction_id);
        v_count := v_count + 1;
    end loop;
    return v_count;
end;
$$;
