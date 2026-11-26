/*
  YatraFlow Site Admin schema for Microsoft SQL Server (dbo).
  Idempotent: creates current tables and adds missing columns to the earlier scaffold.
  Review against the organization-owned production schema before deployment.
*/
SET NOCOUNT ON;
GO

IF OBJECT_ID(N'dbo.sites',N'U') IS NULL
BEGIN
  CREATE TABLE dbo.sites (
    id INT NOT NULL CONSTRAINT PK_sites PRIMARY KEY,
    name NVARCHAR(120) NOT NULL,
    city NVARCHAR(100) NOT NULL,
    capacity INT NOT NULL,
    people_inside INT NULL,
    checked_in_today INT NULL,
    average_dwell_minutes INT NULL,
    people_change_percent FLOAT NULL,
    predicted_tomorrow INT NULL,
    peak_window NVARCHAR(80) NULL,
    updated_at NVARCHAR(40) NOT NULL
  );
END;
GO
IF COL_LENGTH(N'dbo.sites',N'checked_in_today') IS NULL ALTER TABLE dbo.sites ADD checked_in_today INT NULL;
IF COL_LENGTH(N'dbo.sites',N'average_dwell_minutes') IS NULL ALTER TABLE dbo.sites ADD average_dwell_minutes INT NULL;
IF COL_LENGTH(N'dbo.sites',N'people_change_percent') IS NULL ALTER TABLE dbo.sites ADD people_change_percent FLOAT NULL;
IF COL_LENGTH(N'dbo.sites',N'predicted_tomorrow') IS NULL ALTER TABLE dbo.sites ADD predicted_tomorrow INT NULL;
IF COL_LENGTH(N'dbo.sites',N'peak_window') IS NULL ALTER TABLE dbo.sites ADD peak_window NVARCHAR(80) NULL;
GO

IF OBJECT_ID(N'dbo.zones',N'U') IS NULL
BEGIN
  CREATE TABLE dbo.zones (
    id INT NOT NULL CONSTRAINT PK_zones PRIMARY KEY,
    site_id INT NOT NULL,
    name NVARCHAR(120) NOT NULL,
    current_visitors INT NOT NULL CONSTRAINT DF_zones_visitors DEFAULT 0,
    capacity INT NOT NULL CONSTRAINT DF_zones_capacity DEFAULT 0,
    density_percent FLOAT NOT NULL CONSTRAINT DF_zones_density DEFAULT 0,
    level NVARCHAR(50) NOT NULL CONSTRAINT DF_zones_level DEFAULT N'Low',
    updated_at NVARCHAR(40) NULL,
    CONSTRAINT UQ_zones_site_name UNIQUE(site_id,name)
  );
END;
GO
IF COL_LENGTH(N'dbo.zones',N'capacity') IS NULL ALTER TABLE dbo.zones ADD capacity INT NOT NULL CONSTRAINT DF_zones_capacity_migrated DEFAULT 0 WITH VALUES;
IF COL_LENGTH(N'dbo.zones',N'updated_at') IS NULL ALTER TABLE dbo.zones ADD updated_at NVARCHAR(40) NULL;
GO

IF OBJECT_ID(N'dbo.incidents',N'U') IS NULL
BEGIN
  CREATE TABLE dbo.incidents (
    id NVARCHAR(80) NOT NULL CONSTRAINT PK_incidents PRIMARY KEY,
    site_id INT NOT NULL,
    type NVARCHAR(40) NOT NULL,
    priority NVARCHAR(40) NOT NULL,
    title NVARCHAR(200) NOT NULL,
    visitor_name NVARCHAR(160) NOT NULL CONSTRAINT DF_incidents_visitor DEFAULT N'',
    visitor_id NVARCHAR(80) NULL,
    zone_name NVARCHAR(120) NOT NULL CONSTRAINT DF_incidents_zone DEFAULT N'',
    description NVARCHAR(1000) NOT NULL CONSTRAINT DF_incidents_description DEFAULT N'',
    status NVARCHAR(60) NOT NULL CONSTRAINT DF_incidents_status DEFAULT N'Open',
    assigned_to NVARCHAR(160) NULL,
    contact_phone NVARCHAR(40) NULL,
    latitude FLOAT NULL,
    longitude FLOAT NULL,
    resolved_at NVARCHAR(40) NULL,
    updated_at NVARCHAR(40) NULL,
    reported_minutes INT NOT NULL CONSTRAINT DF_incidents_minutes DEFAULT 0,
    created_at NVARCHAR(40) NOT NULL
  );
END;
GO
IF COL_LENGTH(N'dbo.incidents',N'visitor_id') IS NULL ALTER TABLE dbo.incidents ADD visitor_id NVARCHAR(80) NULL;
IF COL_LENGTH(N'dbo.incidents',N'contact_phone') IS NULL ALTER TABLE dbo.incidents ADD contact_phone NVARCHAR(40) NULL;
IF COL_LENGTH(N'dbo.incidents',N'latitude') IS NULL ALTER TABLE dbo.incidents ADD latitude FLOAT NULL;
IF COL_LENGTH(N'dbo.incidents',N'longitude') IS NULL ALTER TABLE dbo.incidents ADD longitude FLOAT NULL;
IF COL_LENGTH(N'dbo.incidents',N'resolved_at') IS NULL ALTER TABLE dbo.incidents ADD resolved_at NVARCHAR(40) NULL;
IF COL_LENGTH(N'dbo.incidents',N'updated_at') IS NULL ALTER TABLE dbo.incidents ADD updated_at NVARCHAR(40) NULL;
GO

IF OBJECT_ID(N'dbo.visitors',N'U') IS NULL
BEGIN
  CREATE TABLE dbo.visitors (
    id NVARCHAR(80) NOT NULL CONSTRAINT PK_visitors PRIMARY KEY,
    site_id INT NOT NULL,
    full_name NVARCHAR(160) NOT NULL,
    phone NVARCHAR(40) NULL,
    registered_at NVARCHAR(40) NULL,
    pass_id NVARCHAR(80) NOT NULL,
    zone_name NVARCHAR(120) NOT NULL,
    entry_time NVARCHAR(40) NOT NULL,
    pass_type NVARCHAR(60) NOT NULL,
    status NVARCHAR(60) NOT NULL CONSTRAINT DF_visitors_status DEFAULT N'On site'
  );
END;
GO
IF COL_LENGTH(N'dbo.visitors',N'phone') IS NULL ALTER TABLE dbo.visitors ADD phone NVARCHAR(40) NULL;
IF COL_LENGTH(N'dbo.visitors',N'registered_at') IS NULL ALTER TABLE dbo.visitors ADD registered_at NVARCHAR(40) NULL;
GO

IF OBJECT_ID(N'dbo.footfall',N'U') IS NULL
BEGIN
  CREATE TABLE dbo.footfall (
    site_id INT NOT NULL,
    date_key CHAR(10) NOT NULL,
    label NVARCHAR(30) NOT NULL,
    series NVARCHAR(40) NOT NULL,
    visitor_count INT NOT NULL,
    CONSTRAINT PK_footfall PRIMARY KEY(site_id,date_key,series)
  );
END;
GO

IF OBJECT_ID(N'dbo.alerts',N'U') IS NULL
BEGIN
  CREATE TABLE dbo.alerts (
    id NVARCHAR(80) NOT NULL CONSTRAINT PK_alerts PRIMARY KEY,
    site_id INT NOT NULL,
    category NVARCHAR(60) NOT NULL,
    title NVARCHAR(200) NOT NULL,
    description NVARCHAR(1000) NOT NULL,
    created_at NVARCHAR(40) NOT NULL
  );
END;
GO

IF OBJECT_ID(N'dbo.admins',N'U') IS NULL
BEGIN
  CREATE TABLE dbo.admins (
    id NVARCHAR(80) NOT NULL CONSTRAINT PK_admins PRIMARY KEY,
    site_id INT NOT NULL,
    full_name NVARCHAR(160) NOT NULL,
    email NVARCHAR(200) NOT NULL,
    role NVARCHAR(80) NOT NULL,
    role_key NVARCHAR(40) NOT NULL CONSTRAINT DF_admins_role_key DEFAULT N'site_admin',
    access_scope NVARCHAR(200) NOT NULL,
    shift_status NVARCHAR(60) NOT NULL,
    active BIT NOT NULL CONSTRAINT DF_admins_active DEFAULT 1,
    password_hash NVARCHAR(400) NULL,
    last_active NVARCHAR(80) NOT NULL,
    last_login NVARCHAR(40) NULL,
    created_at NVARCHAR(40) NULL
  );
END;
GO
IF COL_LENGTH(N'dbo.admins',N'role_key') IS NULL ALTER TABLE dbo.admins ADD role_key NVARCHAR(40) NOT NULL CONSTRAINT DF_admins_role_key_migrated DEFAULT N'site_admin' WITH VALUES;
IF COL_LENGTH(N'dbo.admins',N'active') IS NULL ALTER TABLE dbo.admins ADD active BIT NOT NULL CONSTRAINT DF_admins_active_migrated DEFAULT 1 WITH VALUES;
IF COL_LENGTH(N'dbo.admins',N'password_hash') IS NULL ALTER TABLE dbo.admins ADD password_hash NVARCHAR(400) NULL;
IF COL_LENGTH(N'dbo.admins',N'last_login') IS NULL ALTER TABLE dbo.admins ADD last_login NVARCHAR(40) NULL;
IF COL_LENGTH(N'dbo.admins',N'created_at') IS NULL ALTER TABLE dbo.admins ADD created_at NVARCHAR(40) NULL;
GO
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name=N'UX_admins_email' AND object_id=OBJECT_ID(N'dbo.admins'))
  CREATE UNIQUE INDEX UX_admins_email ON dbo.admins(email);
GO

IF OBJECT_ID(N'dbo.broadcasts',N'U') IS NULL
BEGIN
  CREATE TABLE dbo.broadcasts (
    id NVARCHAR(80) NOT NULL CONSTRAINT PK_broadcasts PRIMARY KEY,
    site_id INT NOT NULL,
    title NVARCHAR(200) NOT NULL,
    message NVARCHAR(1000) NOT NULL,
    category NVARCHAR(60) NOT NULL CONSTRAINT DF_broadcasts_category DEFAULT N'Information',
    zone_name NVARCHAR(120) NULL,
    priority NVARCHAR(40) NOT NULL,
    audience NVARCHAR(200) NOT NULL,
    delivery_status NVARCHAR(80) NOT NULL CONSTRAINT DF_broadcasts_delivery DEFAULT N'pending_provider',
    reached INT NOT NULL CONSTRAINT DF_broadcasts_reached DEFAULT 0,
    created_at NVARCHAR(40) NOT NULL
  );
END;
GO
IF COL_LENGTH(N'dbo.broadcasts',N'category') IS NULL ALTER TABLE dbo.broadcasts ADD category NVARCHAR(60) NOT NULL CONSTRAINT DF_broadcasts_category_migrated DEFAULT N'Information' WITH VALUES;
IF COL_LENGTH(N'dbo.broadcasts',N'zone_name') IS NULL ALTER TABLE dbo.broadcasts ADD zone_name NVARCHAR(120) NULL;
IF COL_LENGTH(N'dbo.broadcasts',N'delivery_status') IS NULL ALTER TABLE dbo.broadcasts ADD delivery_status NVARCHAR(80) NOT NULL CONSTRAINT DF_broadcasts_delivery_migrated DEFAULT N'pending_provider' WITH VALUES;
GO

IF OBJECT_ID(N'dbo.audit_log',N'U') IS NULL
BEGIN
  CREATE TABLE dbo.audit_log (
    id NVARCHAR(80) NOT NULL CONSTRAINT PK_audit_log PRIMARY KEY,
    site_id INT NOT NULL,
    entity_type NVARCHAR(60) NOT NULL,
    entity_id NVARCHAR(100) NOT NULL,
    action NVARCHAR(80) NOT NULL,
    actor NVARCHAR(160) NOT NULL,
    detail NVARCHAR(1000) NOT NULL,
    created_at NVARCHAR(40) NOT NULL
  );
END;
GO

IF OBJECT_ID(N'dbo.admin_sessions',N'U') IS NULL CREATE TABLE dbo.admin_sessions (
  token_hash CHAR(64) NOT NULL CONSTRAINT PK_admin_sessions PRIMARY KEY,
  admin_id NVARCHAR(80) NOT NULL,
  created_at NVARCHAR(40) NOT NULL,
  expires_at NVARCHAR(40) NOT NULL,
  last_seen NVARCHAR(40) NOT NULL,
  ip_address NVARCHAR(80) NULL
);
IF OBJECT_ID(N'dbo.visitor_sessions',N'U') IS NULL CREATE TABLE dbo.visitor_sessions (
  id NVARCHAR(80) NOT NULL CONSTRAINT PK_visitor_sessions PRIMARY KEY,
  external_id NVARCHAR(120) NULL,
  visitor_id NVARCHAR(80) NOT NULL,
  site_id INT NOT NULL,
  zone_name NVARCHAR(120) NULL,
  checked_in_at NVARCHAR(40) NOT NULL,
  checked_out_at NVARCHAR(40) NULL,
  source NVARCHAR(40) NOT NULL CONSTRAINT DF_visitor_sessions_source DEFAULT N'mobile'
);
IF OBJECT_ID(N'dbo.incident_status_history',N'U') IS NULL CREATE TABLE dbo.incident_status_history (
  id NVARCHAR(80) NOT NULL CONSTRAINT PK_incident_status_history PRIMARY KEY,
  incident_id NVARCHAR(80) NOT NULL,
  site_id INT NOT NULL,
  old_status NVARCHAR(60) NULL,
  new_status NVARCHAR(60) NOT NULL,
  actor_admin_id NVARCHAR(80) NULL,
  note NVARCHAR(1000) NULL,
  created_at NVARCHAR(40) NOT NULL
);
IF OBJECT_ID(N'dbo.incident_notes',N'U') IS NULL CREATE TABLE dbo.incident_notes (
  id NVARCHAR(80) NOT NULL CONSTRAINT PK_incident_notes PRIMARY KEY,
  incident_id NVARCHAR(80) NOT NULL,
  site_id INT NOT NULL,
  author_admin_id NVARCHAR(80) NOT NULL,
  note NVARCHAR(1000) NOT NULL,
  created_at NVARCHAR(40) NOT NULL
);
IF OBJECT_ID(N'dbo.emergency_assignments',N'U') IS NULL CREATE TABLE dbo.emergency_assignments (
  id NVARCHAR(80) NOT NULL CONSTRAINT PK_emergency_assignments PRIMARY KEY,
  incident_id NVARCHAR(80) NOT NULL,
  site_id INT NOT NULL,
  responder NVARCHAR(160) NOT NULL,
  provider NVARCHAR(100) NOT NULL,
  request_status NVARCHAR(80) NOT NULL,
  external_reference NVARCHAR(160) NULL,
  created_by NVARCHAR(80) NOT NULL,
  created_at NVARCHAR(40) NOT NULL,
  updated_at NVARCHAR(40) NOT NULL
);
IF OBJECT_ID(N'dbo.crowd_observations',N'U') IS NULL CREATE TABLE dbo.crowd_observations (
  id NVARCHAR(80) NOT NULL CONSTRAINT PK_crowd_observations PRIMARY KEY,
  site_id INT NOT NULL,
  zone_name NVARCHAR(120) NOT NULL,
  visitor_count INT NOT NULL,
  capacity INT NULL,
  density_percent FLOAT NULL,
  source NVARCHAR(100) NOT NULL,
  observed_at NVARCHAR(40) NOT NULL
);
IF OBJECT_ID(N'dbo.crowd_forecasts',N'U') IS NULL CREATE TABLE dbo.crowd_forecasts (
  id NVARCHAR(80) NOT NULL CONSTRAINT PK_crowd_forecasts PRIMARY KEY,
  site_id INT NOT NULL,
  forecast_date CHAR(10) NOT NULL,
  period_start NVARCHAR(40) NULL,
  period_end NVARCHAR(40) NULL,
  predicted_visitors INT NOT NULL,
  model_name NVARCHAR(120) NOT NULL,
  model_version NVARCHAR(80) NULL,
  confidence FLOAT NULL,
  generated_at NVARCHAR(40) NOT NULL,
  source NVARCHAR(120) NOT NULL
);
IF OBJECT_ID(N'dbo.notifications',N'U') IS NULL CREATE TABLE dbo.notifications (
  id NVARCHAR(80) NOT NULL CONSTRAINT PK_notifications PRIMARY KEY,
  site_id INT NOT NULL,
  zone_name NVARCHAR(120) NULL,
  category NVARCHAR(60) NOT NULL,
  severity NVARCHAR(40) NOT NULL,
  title NVARCHAR(200) NOT NULL,
  message NVARCHAR(1000) NOT NULL,
  target_audience NVARCHAR(200) NOT NULL,
  delivery_status NVARCHAR(80) NOT NULL,
  created_by NVARCHAR(80) NOT NULL,
  created_at NVARCHAR(40) NOT NULL
);
IF OBJECT_ID(N'dbo.notification_deliveries',N'U') IS NULL CREATE TABLE dbo.notification_deliveries (
  id NVARCHAR(80) NOT NULL CONSTRAINT PK_notification_deliveries PRIMARY KEY,
  notification_id NVARCHAR(80) NOT NULL,
  visitor_id NVARCHAR(80) NULL,
  channel NVARCHAR(40) NOT NULL,
  delivery_status NVARCHAR(80) NOT NULL,
  created_at NVARCHAR(40) NOT NULL,
  delivered_at NVARCHAR(40) NULL,
  error NVARCHAR(1000) NULL
);
IF OBJECT_ID(N'dbo.app_metadata',N'U') IS NULL CREATE TABLE dbo.app_metadata (
  meta_key NVARCHAR(100) NOT NULL CONSTRAINT PK_app_metadata PRIMARY KEY,
  meta_value NVARCHAR(500) NOT NULL
);
GO

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name=N'IX_incidents_site_status' AND object_id=OBJECT_ID(N'dbo.incidents')) CREATE INDEX IX_incidents_site_status ON dbo.incidents(site_id,status);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name=N'IX_visitors_site' AND object_id=OBJECT_ID(N'dbo.visitors')) CREATE INDEX IX_visitors_site ON dbo.visitors(site_id);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name=N'IX_footfall_site_date' AND object_id=OBJECT_ID(N'dbo.footfall')) CREATE INDEX IX_footfall_site_date ON dbo.footfall(site_id,date_key);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name=N'IX_broadcasts_site_created' AND object_id=OBJECT_ID(N'dbo.broadcasts')) CREATE INDEX IX_broadcasts_site_created ON dbo.broadcasts(site_id,created_at);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name=N'IX_admin_sessions_admin' AND object_id=OBJECT_ID(N'dbo.admin_sessions')) CREATE INDEX IX_admin_sessions_admin ON dbo.admin_sessions(admin_id,expires_at);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name=N'IX_visitor_sessions_site' AND object_id=OBJECT_ID(N'dbo.visitor_sessions')) CREATE INDEX IX_visitor_sessions_site ON dbo.visitor_sessions(site_id,checked_out_at);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name=N'IX_incident_status_history' AND object_id=OBJECT_ID(N'dbo.incident_status_history')) CREATE INDEX IX_incident_status_history ON dbo.incident_status_history(incident_id,created_at);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name=N'IX_crowd_observations_zone_time' AND object_id=OBJECT_ID(N'dbo.crowd_observations')) CREATE INDEX IX_crowd_observations_zone_time ON dbo.crowd_observations(site_id,zone_name,observed_at);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name=N'IX_crowd_forecasts_site_date' AND object_id=OBJECT_ID(N'dbo.crowd_forecasts')) CREATE INDEX IX_crowd_forecasts_site_date ON dbo.crowd_forecasts(site_id,forecast_date);
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name=N'IX_notifications_site_created' AND object_id=OBJECT_ID(N'dbo.notifications')) CREATE INDEX IX_notifications_site_created ON dbo.notifications(site_id,created_at);
GO
