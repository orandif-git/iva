-- The daily and weekly summary goes to the owner by default. The owner's numbers are the ones set on the customer, so the summary job needs to read them.
GRANT SELECT ON platform.tenant_owner_phones TO service_worker;
