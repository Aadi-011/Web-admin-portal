/* Optional demo records for first-time setup. Review/replace demo rows before production use. */
SET NOCOUNT ON;

INSERT INTO dbo.sites (id,name,city,capacity,people_inside,checked_in_today,average_dwell_minutes,people_change_percent,predicted_tomorrow,peak_window,updated_at)
SELECT v.id,v.name,v.city,v.capacity,v.people_inside,v.checked_in_today,v.average_dwell_minutes,v.people_change_percent,v.predicted_tomorrow,v.peak_window,v.updated_at
FROM (VALUES
  (1,N'Amber Fort',N'Jaipur',16000,12480,18624,134,8.0,0,N'',N'2026-11-25T14:32:00+05:30'),
  (2,N'City Palace',N'Jaipur',12000,7460,11524,117,4.2,0,N'',N'2026-11-25T14:32:00+05:30'),
  (3,N'Hawa Mahal',N'Jaipur',9000,6210,9850,95,-2.1,0,N'',N'2026-11-25T14:32:00+05:30')
) AS v(id,name,city,capacity,people_inside,checked_in_today,average_dwell_minutes,people_change_percent,predicted_tomorrow,peak_window,updated_at)
WHERE NOT EXISTS (SELECT 1 FROM dbo.sites s WHERE s.id=v.id);

INSERT INTO dbo.zones (id,site_id,name,current_visitors,capacity,density_percent,level,updated_at)
SELECT v.id,v.site_id,v.name,v.current_visitors,v.capacity,v.density_percent,v.level,N'2026-11-25T14:32:00+05:30'
FROM (VALUES
  (101,1,N'Main Gate',2840,3000,94,N'Critical watch'),(102,1,N'Sheesh Mahal',1920,3100,62,N'Moderate'),(103,1,N'Parking',680,2400,28,N'Low'),(104,1,N'Courtyard',3140,3900,81,N'High'),
  (201,2,N'Main Gate',1640,2450,67,N'Moderate'),(202,2,N'Mubarak Mahal',1830,2400,76,N'High'),(203,2,N'Pritam Chowk',2270,2780,82,N'High'),(204,2,N'Museum Wing',920,2700,34,N'Low'),
  (301,3,N'Front Plaza',2210,2830,78,N'High'),(302,3,N'Main Gate',1760,2100,84,N'High'),(303,3,N'Museum Wing',1140,2375,48,N'Moderate'),(304,3,N'East Approach',820,2828,29,N'Low')
) AS v(id,site_id,name,current_visitors,capacity,density_percent,level)
WHERE NOT EXISTS (SELECT 1 FROM dbo.zones z WHERE z.id=v.id OR (z.site_id=v.site_id AND z.name=v.name));

UPDATE dbo.zones SET capacity=CASE id WHEN 101 THEN 3000 WHEN 102 THEN 3100 WHEN 103 THEN 2400 WHEN 104 THEN 3900 WHEN 201 THEN 2450 WHEN 202 THEN 2400 WHEN 203 THEN 2780 WHEN 204 THEN 2700 WHEN 301 THEN 2830 WHEN 302 THEN 2100 WHEN 303 THEN 2375 WHEN 304 THEN 2828 ELSE capacity END,
  updated_at=COALESCE(updated_at,N'2026-11-25T14:32:00+05:30') WHERE capacity=0 AND id BETWEEN 101 AND 304;

INSERT INTO dbo.incidents (id,site_id,type,priority,title,visitor_name,zone_name,description,status,assigned_to,reported_minutes,created_at)
SELECT v.id,v.site_id,v.type,v.priority,v.title,v.visitor_name,v.zone_name,v.description,v.status,v.assigned_to,v.reported_minutes,v.created_at
FROM (VALUES
  (N'SOS-2026-001',1,N'SOS',N'Critical',N'Visitor assistance',N'Aarav Sharma',N'Main Gate',N'Development sample incident; no mobile GPS/contact feed connected.',N'Assigned',N'Medical team 02',2,N'2026-11-25T14:30:00+05:30'),
  (N'SOS-2026-002',1,N'SOS',N'Critical',N'Visitor assistance',N'Visitor unidentified',N'Courtyard',N'Development sample incident; not received from a connected mobile app.',N'Submitted',NULL,6,N'2026-11-25T14:26:00+05:30'),
  (N'RPT-2026-001',1,N'Report',N'Medium',N'Cleanliness report',N'Visitor report',N'Sheesh Mahal',N'Cleanliness issue reported near the north path; photo attached.',N'Open',NULL,14,N'2026-11-25T14:18:00+05:30'),
  (N'SOS-2026-003',1,N'SOS',N'Critical',N'First-aid support',N'Leela Patel',N'Sheesh Mahal',N'Development sample incident; no external dispatch service connected.',N'Assigned',N'Medical team 01',16,N'2026-11-25T14:16:00+05:30'),
  (N'SYS-2026-018',1,N'Crowd',N'Medium',N'Density threshold exceeded',N'',N'Main Gate',N'Automated alert: zone density is above 90%.',N'Monitoring',N'Site supervisor',21,N'2026-11-25T14:11:00+05:30'),
  (N'WX-2026-004',1,N'Weather',N'Advisory',N'Heavy rain advisory',N'',N'All zones',N'Local weather feed reports heavy rain in Jaipur.',N'Resolved',N'Control room',20,N'2026-11-25T14:12:00+05:30'),
  (N'SOS-2026-004',2,N'SOS',N'Critical',N'Visitor assistance',N'Rohit Jain',N'Pritam Chowk',N'Development sample incident; no mobile app source connected.',N'Submitted',NULL,4,N'2026-11-25T14:28:00+05:30')
) AS v(id,site_id,type,priority,title,visitor_name,zone_name,description,status,assigned_to,reported_minutes,created_at)
WHERE NOT EXISTS (SELECT 1 FROM dbo.incidents i WHERE i.id=v.id);

INSERT INTO dbo.visitors (id,site_id,full_name,pass_id,zone_name,entry_time,pass_type,status)
SELECT v.id,v.site_id,v.full_name,v.pass_id,v.zone_name,v.entry_time,v.pass_type,v.status
FROM (VALUES
  (N'V-10832',1,N'Aarav Sharma',N'VF-10832',N'Main Gate',N'10:42 AM',N'Family',N'Assistance'),
  (N'V-10791',1,N'Meera Kapoor',N'VF-10791',N'Courtyard',N'10:21 AM',N'VIP',N'On site'),
  (N'V-10662',1,N'Kabir Singh',N'VF-10662',N'Sheesh Mahal',N'9:58 AM',N'Standard',N'On site'),
  (N'V-10550',1,N'Priya Nair',N'VF-10550',N'Parking',N'9:44 AM',N'Family',N'On site'),
  (N'V-10412',1,N'Dev Malhotra',N'VF-10412',N'Main Gate',N'9:31 AM',N'VIP',N'Assistance'),
  (N'V-10220',1,N'Anaya Rao',N'VF-10220',N'Courtyard',N'9:12 AM',N'Standard',N'On site'),
  (N'V-20931',2,N'Rohit Jain',N'CP-20931',N'Pritam Chowk',N'10:14 AM',N'Standard',N'Assistance'),
  (N'V-30612',3,N'Isha Verma',N'HM-30612',N'Front Plaza',N'11:02 AM',N'Family',N'On site')
) AS v(id,site_id,full_name,pass_id,zone_name,entry_time,pass_type,status)
WHERE NOT EXISTS (SELECT 1 FROM dbo.visitors p WHERE p.id=v.id);

INSERT INTO dbo.alerts (id,site_id,category,title,description,created_at)
SELECT v.id,v.site_id,v.category,v.title,v.description,v.created_at
FROM (VALUES
  (N'ALT-2026-001',1,N'Crowd',N'Main Gate over 90% density',N'Suggest alternate route via East Gate · 3 min ago',N'2026-11-25T14:29:00+05:30'),
  (N'ALT-2026-002',1,N'Weather',N'Heavy rain advisory for Jaipur',N'Source: local weather feed · 20 min ago',N'2026-11-25T14:12:00+05:30'),
  (N'ALT-2026-003',1,N'Security',N'Response team on standby',N'Courtyard post · checked in 8 min ago',N'2026-11-25T14:24:00+05:30'),
  (N'ALT-2026-004',2,N'Crowd',N'Pritam Chowk nearing high density',N'Route guests through the museum wing.',N'2026-11-25T14:25:00+05:30'),
  (N'ALT-2026-005',3,N'Weather',N'Light rain expected this afternoon',N'Review covered waiting areas.',N'2026-11-25T14:21:00+05:30')
) AS v(id,site_id,category,title,description,created_at)
WHERE NOT EXISTS (SELECT 1 FROM dbo.alerts a WHERE a.id=v.id);

INSERT INTO dbo.admins (id,site_id,full_name,email,role,role_key,access_scope,shift_status,active,password_hash,last_active,last_login,created_at)
SELECT v.id,v.site_id,v.full_name,v.email,v.role,v.role_key,v.access_scope,v.shift_status,1,NULL,v.last_active,NULL,N'2026-11-25T14:32:00+05:30'
FROM (VALUES
  (N'ADM-001',1,N'Ava Desai',N'ava.desai@yatraflow.in',N'Super Admin',N'super_admin',N'Full site access',N'On shift',N'Now'),
  (N'ADM-002',1,N'Rahul Mehta',N'rahul.mehta@yatraflow.in',N'Emergency Operator',N'emergency_operator',N'Incidents · alerts',N'On shift',N'4 min ago'),
  (N'ADM-003',1,N'Sana Khan',N'sana.khan@yatraflow.in',N'Site Admin',N'site_admin',N'Visitors · reports',N'On shift',N'18 min ago'),
  (N'ADM-004',1,N'Arjun Singh',N'arjun.singh@yatraflow.in',N'Analytics Viewer',N'analytics_viewer',N'Analytics · live map',N'Off shift',N'Yesterday'),
  (N'ADM-005',2,N'Kavita Rao',N'kavita.rao@yatraflow.in',N'Site Admin',N'site_admin',N'Full site access',N'On shift',N'Now'),
  (N'ADM-006',3,N'Devika Singh',N'devika.singh@yatraflow.in',N'Site Admin',N'site_admin',N'Full site access',N'On shift',N'Now')
) AS v(id,site_id,full_name,email,role,role_key,access_scope,shift_status,last_active)
WHERE NOT EXISTS (SELECT 1 FROM dbo.admins a WHERE a.id=v.id);

INSERT INTO dbo.broadcasts (id,site_id,title,message,category,zone_name,priority,audience,delivery_status,reached,created_at)
SELECT v.id,v.site_id,v.title,v.message,v.category,v.zone_name,v.priority,v.audience,N'pending_provider',0,v.created_at
FROM (VALUES
  (N'BR-2026-101',1,N'Heavy rain advisory',N'Please move to covered areas and follow staff directions while the rain advisory is active.',N'Weather',NULL,N'warning',N'all_visitors',N'2026-11-25T12:10:00+05:30'),
  (N'BR-2026-102',1,N'Main Gate queue update',N'For a quicker entry, please use the East Gate. Follow the wayfinding signs.',N'Information',N'Main Gate',N'info',N'zone:Main Gate',N'2026-11-25T10:42:00+05:30'),
  (N'BR-2026-103',1,N'Lost child support point',N'Families needing help can visit the visitor support desk beside the main entrance.',N'Safety',NULL,N'warning',N'all_visitors',N'2026-11-24T15:16:00+05:30')
) AS v(id,site_id,title,message,category,zone_name,priority,audience,created_at)
WHERE NOT EXISTS (SELECT 1 FROM dbo.broadcasts b WHERE b.id=v.id);

/* Generate 90 days of illustrative historical footfall series. No forecast is generated. */
DECLARE @DemoToday DATE = '2026-11-25';
;WITH DayNumbers AS (
  SELECT 0 AS n
  UNION ALL SELECT n + 1 FROM DayNumbers WHERE n < 89
), Dates AS (
  SELECT DATEADD(DAY, n, DATEADD(DAY, -89, @DemoToday)) AS day_date, n AS day_index FROM DayNumbers
), SiteDates AS (
  SELECT s.id AS site_id, d.day_date, d.day_index,
    CASE WHEN s.id=1 THEN 1.0 WHEN s.id=2 THEN 0.77 ELSE 0.66 END AS site_factor,
    CASE WHEN DATEDIFF(DAY, CONVERT(DATE,'20000102'), d.day_date) % 7 IN (0,6) THEN 1.10 ELSE 1.0 END AS weekend_factor
  FROM dbo.sites s CROSS JOIN Dates d
), Calculated AS (
  SELECT site_id,day_date,day_index,
    CONVERT(INT,ROUND((7700 + SIN(day_index*0.23)*1150 + SIN(day_index*0.071)*650) * site_factor * weekend_factor,0)) AS v24,
    CONVERT(INT,ROUND((9200 + SIN(day_index*0.23)*1200 + SIN(day_index*0.071)*680) * site_factor * weekend_factor,0)) AS v25,
    CONVERT(INT,ROUND((10400 + SIN(day_index*0.23)*1240 + SIN(day_index*0.071)*700 + day_index*18) * site_factor * weekend_factor,0)) AS v26
  FROM SiteDates
), SeriesValues AS (
  SELECT c.site_id,c.day_date,s.series_name,
    CASE WHEN s.series_name=N'avg_2024' THEN c.v24 WHEN s.series_name=N'avg_2025' THEN c.v25 ELSE c.v26 END AS visitor_count
  FROM Calculated c
  CROSS APPLY (VALUES (N'avg_2024'),(N'avg_2025'),(N'actual_2026')) s(series_name)
)
INSERT INTO dbo.footfall (site_id,date_key,label,series,visitor_count)
SELECT sv.site_id,CONVERT(CHAR(10),sv.day_date,23),CONCAT(DAY(sv.day_date),N' ',LEFT(DATENAME(MONTH,sv.day_date),3)),sv.series_name,sv.visitor_count
FROM SeriesValues sv
WHERE NOT EXISTS (SELECT 1 FROM dbo.footfall f WHERE f.site_id=sv.site_id AND f.date_key=CONVERT(CHAR(10),sv.day_date,23) AND f.series=sv.series_name)
OPTION (MAXRECURSION 100);

MERGE dbo.app_metadata AS target
USING (VALUES (N'data_mode',N'demo'),(N'mobile_integration',N'not_connected')) AS source(meta_key,meta_value)
ON target.meta_key=source.meta_key
WHEN MATCHED AND source.meta_key=N'data_mode' THEN UPDATE SET target.meta_value=N'demo'
WHEN NOT MATCHED THEN INSERT (meta_key,meta_value) VALUES (source.meta_key,source.meta_value);
