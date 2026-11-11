-- "Vehicle has stayed too long" is now set in minutes, not hours. Settings already saved in hours are converted (8 hours becomes 480 minutes).
UPDATE app.alert_rules
   SET params = (params - 'max_hours') || jsonb_build_object('max_minutes', LEAST(10080, GREATEST(5, round((params->>'max_hours')::numeric * 60)::int)))
 WHERE rule_type = 'vehicle_overstay' AND params ? 'max_hours' AND NOT params ? 'max_minutes';
