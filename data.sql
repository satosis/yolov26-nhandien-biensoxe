--
-- PostgreSQL database dump
--

\restrict EwOIx8KQy02uiyTFhSjuzRNjYQ5upVe7QQEz2MiaB3iPmHZ8DoRA2n1WV8ivXrZ

-- Dumped from database version 16.14
-- Dumped by pg_dump version 16.14

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: admin_seeds; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.admin_seeds (
    id integer NOT NULL,
    username text NOT NULL,
    note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.admin_seeds OWNER TO camera_user;

--
-- Name: admin_seeds_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.admin_seeds_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.admin_seeds_id_seq OWNER TO camera_user;

--
-- Name: admin_seeds_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: camera_user
--

ALTER SEQUENCE public.admin_seeds_id_seq OWNED BY public.admin_seeds.id;


--
-- Name: app_settings; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.app_settings (
    key text NOT NULL,
    value text,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.app_settings OWNER TO camera_user;

--
-- Name: camera_providers; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.camera_providers (
    provider_key text NOT NULL,
    name text NOT NULL,
    api_base_url text,
    api_doc_url text,
    rtsp_template text,
    onvif_port integer DEFAULT 80 NOT NULL,
    default_rtsp_port integer DEFAULT 554 NOT NULL,
    default_http_port integer DEFAULT 80 NOT NULL,
    notes text,
    logo_url text,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.camera_providers OWNER TO camera_user;

--
-- Name: camera_providers_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.camera_providers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.camera_providers_id_seq OWNER TO camera_user;

--
-- Name: camera_status_log; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.camera_status_log (
    id integer NOT NULL,
    camera_id text NOT NULL,
    status text,
    event_type text,
    notes text,
    logged_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.camera_status_log OWNER TO camera_user;

--
-- Name: camera_status_log_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.camera_status_log_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.camera_status_log_id_seq OWNER TO camera_user;

--
-- Name: camera_status_log_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: camera_user
--

ALTER SEQUENCE public.camera_status_log_id_seq OWNED BY public.camera_status_log.id;


--
-- Name: cameras; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.cameras (
    id integer NOT NULL,
    camera_name text NOT NULL,
    camera_type text DEFAULT 'rtsp'::text NOT NULL,
    imou_device_id text,
    imou_channel_id text DEFAULT '0'::text NOT NULL,
    rtsp_url text,
    onvif_rtsp_detected text,
    camera_ip text,
    camera_port integer DEFAULT 554 NOT NULL,
    provider_keys jsonb DEFAULT '{}'::jsonb NOT NULL,
    site_ids integer[] DEFAULT ARRAY[]::integer[],
    is_active boolean DEFAULT true NOT NULL,
    is_streaming boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.cameras OWNER TO camera_user;

--
-- Name: cameras_asset; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.cameras_asset (
    id integer NOT NULL,
    cam_id text NOT NULL,
    name text,
    ip text,
    mac text,
    serial text,
    model text,
    firmware text,
    rtsp_url text,
    onvif_port integer,
    onvif_profiles jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.cameras_asset OWNER TO camera_user;

--
-- Name: cameras_asset_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.cameras_asset_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.cameras_asset_id_seq OWNER TO camera_user;

--
-- Name: cameras_asset_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: camera_user
--

ALTER SEQUENCE public.cameras_asset_id_seq OWNED BY public.cameras_asset.id;


--
-- Name: cameras_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.cameras_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.cameras_id_seq OWNER TO camera_user;

--
-- Name: cameras_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: camera_user
--

ALTER SEQUENCE public.cameras_id_seq OWNED BY public.cameras.id;


--
-- Name: cargo_trips; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.cargo_trips (
    id integer NOT NULL,
    vehicle_id integer NOT NULL,
    plate_number text NOT NULL,
    departure_time timestamp with time zone NOT NULL,
    return_time timestamp with time zone,
    origin text,
    destination text,
    fuel_liters numeric(12,2),
    fuel_price_per_liter numeric(14,2),
    fuel_cost_total numeric(14,2) GENERATED ALWAYS AS ((fuel_liters * fuel_price_per_liter)) STORED,
    freight_revenue numeric(14,2),
    distance_km numeric(12,2),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.cargo_trips OWNER TO camera_user;

--
-- Name: cargo_trips_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.cargo_trips_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.cargo_trips_id_seq OWNER TO camera_user;

--
-- Name: cargo_trips_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: camera_user
--

ALTER SEQUENCE public.cargo_trips_id_seq OWNED BY public.cargo_trips.id;


--
-- Name: door_providers; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.door_providers (
    provider_key text NOT NULL,
    name text NOT NULL,
    connection_type text,
    api_doc_url text,
    config_template text,
    notes text,
    logo_url text,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.door_providers OWNER TO camera_user;

--
-- Name: door_providers_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.door_providers_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.door_providers_id_seq OWNER TO camera_user;

--
-- Name: inquiries; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.inquiries (
    id integer NOT NULL,
    full_name text NOT NULL,
    phone text NOT NULL,
    company_name text DEFAULT ''::text NOT NULL,
    demand text DEFAULT ''::text NOT NULL,
    source_page text DEFAULT 'home'::text NOT NULL,
    is_read boolean DEFAULT false NOT NULL,
    note text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.inquiries OWNER TO camera_user;

--
-- Name: inquiries_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.inquiries_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.inquiries_id_seq OWNER TO camera_user;

--
-- Name: inquiries_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: camera_user
--

ALTER SEQUENCE public.inquiries_id_seq OWNED BY public.inquiries.id;


--
-- Name: kyc_documents; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.kyc_documents (
    id integer NOT NULL,
    user_id integer NOT NULL,
    doc_type text NOT NULL,
    file_path text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    uploaded_at timestamp with time zone DEFAULT now() NOT NULL,
    reviewed_at timestamp with time zone,
    reviewed_by text
);


ALTER TABLE public.kyc_documents OWNER TO camera_user;

--
-- Name: kyc_documents_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.kyc_documents_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.kyc_documents_id_seq OWNER TO camera_user;

--
-- Name: kyc_documents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: camera_user
--

ALTER SEQUENCE public.kyc_documents_id_seq OWNED BY public.kyc_documents.id;


--
-- Name: legal_hold; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.legal_hold (
    id integer NOT NULL,
    file_path text NOT NULL,
    reason text,
    held_by text,
    held_at timestamp with time zone DEFAULT now() NOT NULL,
    released_at timestamp with time zone
);


ALTER TABLE public.legal_hold OWNER TO camera_user;

--
-- Name: legal_hold_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.legal_hold_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.legal_hold_id_seq OWNER TO camera_user;

--
-- Name: legal_hold_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: camera_user
--

ALTER SEQUENCE public.legal_hold_id_seq OWNED BY public.legal_hold.id;


--
-- Name: monthly_reports; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.monthly_reports (
    id integer NOT NULL,
    report_month date NOT NULL,
    data jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.monthly_reports OWNER TO camera_user;

--
-- Name: monthly_reports_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.monthly_reports_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.monthly_reports_id_seq OWNER TO camera_user;

--
-- Name: monthly_reports_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: camera_user
--

ALTER SEQUENCE public.monthly_reports_id_seq OWNED BY public.monthly_reports.id;


--
-- Name: news; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.news (
    id integer NOT NULL,
    slug text,
    title text NOT NULL,
    category text,
    summary text,
    content text,
    image text,
    publish_date timestamp with time zone DEFAULT now() NOT NULL,
    is_published boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.news OWNER TO camera_user;

--
-- Name: news_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.news_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.news_id_seq OWNER TO camera_user;

--
-- Name: news_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: camera_user
--

ALTER SEQUENCE public.news_id_seq OWNED BY public.news.id;


--
-- Name: ocr_training_samples; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.ocr_training_samples (
    id integer NOT NULL,
    plate_image_path text NOT NULL,
    ocr_text text,
    confidence double precision,
    camera_id text,
    corrected_text text,
    reviewed_by text,
    reviewed_at timestamp with time zone,
    status text DEFAULT 'pending'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.ocr_training_samples OWNER TO camera_user;

--
-- Name: ocr_training_samples_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.ocr_training_samples_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.ocr_training_samples_id_seq OWNER TO camera_user;

--
-- Name: ocr_training_samples_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: camera_user
--

ALTER SEQUENCE public.ocr_training_samples_id_seq OWNED BY public.ocr_training_samples.id;


--
-- Name: pending_plates; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.pending_plates (
    id integer NOT NULL,
    pending_id text NOT NULL,
    event_id integer,
    plate_raw text,
    plate_norm text NOT NULL,
    first_seen_utc timestamp with time zone DEFAULT now() NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    site_id integer,
    image_path text,
    plate_type text DEFAULT 'white'::text NOT NULL,
    confirmed_at_utc timestamp with time zone,
    confirmed_by text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.pending_plates OWNER TO camera_user;

--
-- Name: pending_plates_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.pending_plates_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.pending_plates_id_seq OWNER TO camera_user;

--
-- Name: pending_plates_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: camera_user
--

ALTER SEQUENCE public.pending_plates_id_seq OWNED BY public.pending_plates.id;


--
-- Name: plate_events; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.plate_events (
    id integer NOT NULL,
    event_time timestamp with time zone DEFAULT now() NOT NULL,
    camera_id text,
    site_id integer,
    vehicle_type text,
    plate_number text,
    confidence double precision,
    image_path text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.plate_events OWNER TO camera_user;

--
-- Name: plate_events_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.plate_events_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.plate_events_id_seq OWNER TO camera_user;

--
-- Name: plate_events_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: camera_user
--

ALTER SEQUENCE public.plate_events_id_seq OWNED BY public.plate_events.id;


--
-- Name: plate_whitelist; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.plate_whitelist (
    id integer NOT NULL,
    plate_number text NOT NULL,
    list_type text DEFAULT 'white'::text NOT NULL,
    owner_name text,
    added_by text,
    site_ids jsonb DEFAULT '[]'::jsonb NOT NULL,
    note text,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    fuel_consumption numeric(6,2)
);


ALTER TABLE public.plate_whitelist OWNER TO camera_user;

--
-- Name: plate_whitelist_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.plate_whitelist_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.plate_whitelist_id_seq OWNER TO camera_user;

--
-- Name: plate_whitelist_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: camera_user
--

ALTER SEQUENCE public.plate_whitelist_id_seq OWNED BY public.plate_whitelist.id;


--
-- Name: projects; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.projects (
    id integer NOT NULL,
    name text NOT NULL,
    location text,
    area text,
    height text,
    bays text,
    load_capacity text,
    image text,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.projects OWNER TO camera_user;

--
-- Name: projects_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.projects_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.projects_id_seq OWNER TO camera_user;

--
-- Name: projects_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: camera_user
--

ALTER SEQUENCE public.projects_id_seq OWNED BY public.projects.id;


--
-- Name: roles; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.roles (
    id integer NOT NULL,
    role_name text NOT NULL,
    allowed_menus text DEFAULT ''::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.roles OWNER TO camera_user;

--
-- Name: roles_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.roles_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.roles_id_seq OWNER TO camera_user;

--
-- Name: roles_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: camera_user
--

ALTER SEQUENCE public.roles_id_seq OWNED BY public.roles.id;


--
-- Name: rolling_doors; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.rolling_doors (
    id integer NOT NULL,
    name text NOT NULL,
    supplier_key text,
    site_id integer,
    relay_type text DEFAULT 'http'::text NOT NULL,
    relay_config text,
    is_active boolean DEFAULT true NOT NULL,
    camera_id integer,
    auto_close_enabled boolean DEFAULT false NOT NULL,
    auto_close_delay_sec integer DEFAULT 300 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.rolling_doors OWNER TO camera_user;

--
-- Name: rolling_doors_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.rolling_doors_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.rolling_doors_id_seq OWNER TO camera_user;

--
-- Name: rolling_doors_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: camera_user
--

ALTER SEQUENCE public.rolling_doors_id_seq OWNED BY public.rolling_doors.id;


--
-- Name: sites; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.sites (
    id integer NOT NULL,
    site_name text NOT NULL,
    address text DEFAULT ''::text NOT NULL,
    description text DEFAULT ''::text NOT NULL,
    total_slots integer DEFAULT 0 NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.sites OWNER TO camera_user;

--
-- Name: sites_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.sites_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.sites_id_seq OWNER TO camera_user;

--
-- Name: sites_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: camera_user
--

ALTER SEQUENCE public.sites_id_seq OWNED BY public.sites.id;


--
-- Name: stranger_logs; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.stranger_logs (
    id integer NOT NULL,
    camera_id text,
    similarity double precision,
    image_path text,
    site_id integer,
    face_embedding bytea,
    status text DEFAULT 'new'::text NOT NULL,
    direction text DEFAULT 'frontal'::text NOT NULL,
    converted_user_id integer,
    note text,
    event_time timestamp with time zone DEFAULT now() NOT NULL,
    seen_count integer DEFAULT 1 NOT NULL,
    last_seen timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.stranger_logs OWNER TO camera_user;

--
-- Name: stranger_logs_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.stranger_logs_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.stranger_logs_id_seq OWNER TO camera_user;

--
-- Name: stranger_logs_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: camera_user
--

ALTER SEQUENCE public.stranger_logs_id_seq OWNED BY public.stranger_logs.id;


--
-- Name: telegram_bots; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.telegram_bots (
    id integer NOT NULL,
    bot_name text NOT NULL,
    token text NOT NULL,
    chat_id text NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.telegram_bots OWNER TO camera_user;

--
-- Name: telegram_bots_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.telegram_bots_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.telegram_bots_id_seq OWNER TO camera_user;

--
-- Name: telegram_bots_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: camera_user
--

ALTER SEQUENCE public.telegram_bots_id_seq OWNED BY public.telegram_bots.id;


--
-- Name: users; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.users (
    id integer NOT NULL,
    username text NOT NULL,
    hashed_password text NOT NULL,
    role_id integer,
    full_name text,
    note text,
    site_ids integer[] DEFAULT ARRAY[]::integer[],
    person_identity text,
    phone text,
    email text,
    date_of_birth date,
    id_number text,
    kyc_level integer DEFAULT 0 NOT NULL,
    kyc_submitted_at timestamp with time zone,
    kyc_reviewed_at timestamp with time zone,
    kyc_reviewed_by text,
    kyc_reject_reason text,
    remember_token text,
    remember_token_expires timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.users OWNER TO camera_user;

--
-- Name: users_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.users_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.users_id_seq OWNER TO camera_user;

--
-- Name: users_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: camera_user
--

ALTER SEQUENCE public.users_id_seq OWNED BY public.users.id;


--
-- Name: vehicle_sessions; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.vehicle_sessions (
    id integer NOT NULL,
    plate_norm text NOT NULL,
    vehicle_type text,
    camera_id_in text,
    camera_id_out text,
    entry_time timestamp with time zone DEFAULT now() NOT NULL,
    exit_time timestamp with time zone,
    entry_image text,
    exit_image text,
    site_id integer,
    status text DEFAULT 'parking'::text NOT NULL,
    stay_duration integer,
    fee double precision DEFAULT 0 NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.vehicle_sessions OWNER TO camera_user;

--
-- Name: vehicle_sessions_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.vehicle_sessions_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.vehicle_sessions_id_seq OWNER TO camera_user;

--
-- Name: vehicle_sessions_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: camera_user
--

ALTER SEQUENCE public.vehicle_sessions_id_seq OWNED BY public.vehicle_sessions.id;


--
-- Name: video_recordings; Type: TABLE; Schema: public; Owner: camera_user
--

CREATE TABLE public.video_recordings (
    id integer NOT NULL,
    cam_id text,
    site_id integer,
    event_type text,
    event_text text,
    file_path text NOT NULL,
    thumbnail_path text,
    start_time timestamp with time zone DEFAULT now() NOT NULL,
    duration_seconds integer,
    file_size_bytes bigint,
    is_favorite boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.video_recordings OWNER TO camera_user;

--
-- Name: video_recordings_id_seq; Type: SEQUENCE; Schema: public; Owner: camera_user
--

CREATE SEQUENCE public.video_recordings_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


ALTER SEQUENCE public.video_recordings_id_seq OWNER TO camera_user;

--
-- Name: video_recordings_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: camera_user
--

ALTER SEQUENCE public.video_recordings_id_seq OWNED BY public.video_recordings.id;


--
-- Name: admin_seeds id; Type: DEFAULT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.admin_seeds ALTER COLUMN id SET DEFAULT nextval('public.admin_seeds_id_seq'::regclass);


--
-- Name: camera_status_log id; Type: DEFAULT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.camera_status_log ALTER COLUMN id SET DEFAULT nextval('public.camera_status_log_id_seq'::regclass);


--
-- Name: cameras id; Type: DEFAULT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.cameras ALTER COLUMN id SET DEFAULT nextval('public.cameras_id_seq'::regclass);


--
-- Name: cameras_asset id; Type: DEFAULT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.cameras_asset ALTER COLUMN id SET DEFAULT nextval('public.cameras_asset_id_seq'::regclass);


--
-- Name: cargo_trips id; Type: DEFAULT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.cargo_trips ALTER COLUMN id SET DEFAULT nextval('public.cargo_trips_id_seq'::regclass);


--
-- Name: inquiries id; Type: DEFAULT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.inquiries ALTER COLUMN id SET DEFAULT nextval('public.inquiries_id_seq'::regclass);


--
-- Name: kyc_documents id; Type: DEFAULT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.kyc_documents ALTER COLUMN id SET DEFAULT nextval('public.kyc_documents_id_seq'::regclass);


--
-- Name: legal_hold id; Type: DEFAULT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.legal_hold ALTER COLUMN id SET DEFAULT nextval('public.legal_hold_id_seq'::regclass);


--
-- Name: monthly_reports id; Type: DEFAULT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.monthly_reports ALTER COLUMN id SET DEFAULT nextval('public.monthly_reports_id_seq'::regclass);


--
-- Name: news id; Type: DEFAULT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.news ALTER COLUMN id SET DEFAULT nextval('public.news_id_seq'::regclass);


--
-- Name: ocr_training_samples id; Type: DEFAULT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.ocr_training_samples ALTER COLUMN id SET DEFAULT nextval('public.ocr_training_samples_id_seq'::regclass);


--
-- Name: pending_plates id; Type: DEFAULT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.pending_plates ALTER COLUMN id SET DEFAULT nextval('public.pending_plates_id_seq'::regclass);


--
-- Name: plate_events id; Type: DEFAULT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.plate_events ALTER COLUMN id SET DEFAULT nextval('public.plate_events_id_seq'::regclass);


--
-- Name: plate_whitelist id; Type: DEFAULT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.plate_whitelist ALTER COLUMN id SET DEFAULT nextval('public.plate_whitelist_id_seq'::regclass);


--
-- Name: projects id; Type: DEFAULT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.projects ALTER COLUMN id SET DEFAULT nextval('public.projects_id_seq'::regclass);


--
-- Name: roles id; Type: DEFAULT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.roles ALTER COLUMN id SET DEFAULT nextval('public.roles_id_seq'::regclass);


--
-- Name: rolling_doors id; Type: DEFAULT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.rolling_doors ALTER COLUMN id SET DEFAULT nextval('public.rolling_doors_id_seq'::regclass);


--
-- Name: sites id; Type: DEFAULT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.sites ALTER COLUMN id SET DEFAULT nextval('public.sites_id_seq'::regclass);


--
-- Name: stranger_logs id; Type: DEFAULT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.stranger_logs ALTER COLUMN id SET DEFAULT nextval('public.stranger_logs_id_seq'::regclass);


--
-- Name: telegram_bots id; Type: DEFAULT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.telegram_bots ALTER COLUMN id SET DEFAULT nextval('public.telegram_bots_id_seq'::regclass);


--
-- Name: users id; Type: DEFAULT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.users ALTER COLUMN id SET DEFAULT nextval('public.users_id_seq'::regclass);


--
-- Name: vehicle_sessions id; Type: DEFAULT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.vehicle_sessions ALTER COLUMN id SET DEFAULT nextval('public.vehicle_sessions_id_seq'::regclass);


--
-- Name: video_recordings id; Type: DEFAULT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.video_recordings ALTER COLUMN id SET DEFAULT nextval('public.video_recordings_id_seq'::regclass);


--
-- Data for Name: admin_seeds; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.admin_seeds (id, username, note, created_at) FROM stdin;
1	admin	Seeded default admin account	2026-05-16 07:34:13.832404+00
\.


--
-- Data for Name: app_settings; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.app_settings (key, value, updated_at) FROM stdin;
HIKVISION_USERNAME		2026-03-30 10:24:44.136295+00
HIKVISION_PASSWORD		2026-03-30 10:24:44.136295+00
HIKVISION_HOST		2026-03-30 10:24:44.136295+00
HIKVISION_APP_KEY		2026-03-30 10:24:44.136295+00
HIKVISION_APP_SECRET		2026-03-30 10:24:44.136295+00
HIKVISION_EMAIL		2026-03-30 10:24:44.136295+00
DAHUA_USERNAME		2026-03-30 10:24:44.136295+00
DAHUA_PASSWORD		2026-03-30 10:24:44.136295+00
DAHUA_HOST		2026-03-30 10:24:44.136295+00
DAHUA_CLIENT_ID		2026-03-30 10:24:44.136295+00
DAHUA_CLIENT_SECRET		2026-03-30 10:24:44.136295+00
DAHUA_EMAIL		2026-03-30 10:24:44.136295+00
REOLINK_USERNAME		2026-03-30 10:24:44.136295+00
REOLINK_PASSWORD		2026-03-30 10:24:44.136295+00
REOLINK_HOST		2026-03-30 10:24:44.136295+00
TAPO_USERNAME		2026-03-30 10:24:44.136295+00
TAPO_PASSWORD		2026-03-30 10:24:44.136295+00
TAPO_HOST		2026-03-30 10:24:44.136295+00
TAPO_ONVIF_PORT	2020	2026-03-30 10:24:44.136295+00
AXIS_USERNAME		2026-03-30 10:24:44.136295+00
AXIS_PASSWORD		2026-03-30 10:24:44.136295+00
AXIS_HOST		2026-03-30 10:24:44.136295+00
AXIS_API_KEY		2026-03-30 10:24:44.136295+00
UNIVIEW_USERNAME		2026-03-30 10:24:44.136295+00
UNIVIEW_PASSWORD		2026-03-30 10:24:44.136295+00
UNIVIEW_HOST		2026-03-30 10:24:44.136295+00
UNIVIEW_APP_ID		2026-03-30 10:24:44.136295+00
UNIVIEW_APP_SECRET		2026-03-30 10:24:44.136295+00
UNIVIEW_EMAIL		2026-03-30 10:24:44.136295+00
HANWHA_USERNAME		2026-03-30 10:24:44.136295+00
HANWHA_PASSWORD		2026-03-30 10:24:44.136295+00
HANWHA_HOST		2026-03-30 10:24:44.136295+00
HANWHA_API_KEY		2026-03-30 10:24:44.136295+00
HANWHA_EMAIL		2026-03-30 10:24:44.136295+00
BOSCH_USERNAME		2026-03-30 10:24:44.136295+00
BOSCH_PASSWORD		2026-03-30 10:24:44.136295+00
BOSCH_HOST		2026-03-30 10:24:44.136295+00
BOSCH_SDK_TOKEN		2026-03-30 10:24:44.136295+00
RTSP_USERNAME		2026-03-30 10:24:44.136295+00
RTSP_PASSWORD		2026-03-30 10:24:44.136295+00
RTSP_RTSP_URL		2026-03-30 10:24:44.136295+00
ONVIF_USERNAME		2026-03-30 10:24:44.136295+00
ONVIF_PASSWORD		2026-03-30 10:24:44.136295+00
ONVIF_HOST		2026-03-30 10:24:44.136295+00
ONVIF_ONVIF_PORT	80	2026-03-30 10:24:44.136295+00
WEBRTC_TURN_URL		2026-03-30 10:24:44.136295+00
WEBRTC_TURN_USERNAME		2026-03-30 10:24:44.136295+00
WEBRTC_TURN_PASSWORD		2026-03-30 10:24:44.136295+00
IMOU_APP_ID	lc9f9e2d2dcc094a61	2026-03-31 10:52:38.274712+00
IMOU_APP_SECRET	8676588d325b496c9e3171ba32744f	2026-03-31 10:52:38.274712+00
IMOU_MK	bB123456789@	2026-03-31 10:52:38.274712+00
IMOU_DEVICE_ID	5552ABJPSFFB063	2026-03-31 10:52:38.274712+00
IMOU_EMAIL	hungnmk5@gmail.com	2026-03-31 10:52:38.274712+00
LAST_DISCOVERY_AT	2026-09-19 13:05:59	2026-09-19 06:05:59.238342+00
\.


--
-- Data for Name: camera_providers; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.camera_providers (provider_key, name, api_base_url, api_doc_url, rtsp_template, onvif_port, default_rtsp_port, default_http_port, notes, logo_url, is_active, created_at, updated_at) FROM stdin;
imou	Imou (Dahua Consumer)	https://open.imoulife.com	https://open.imoulife.com/book/index.html	rtsp://{user}:{password}@{ip}:{port}/cam/realmonitor?channel={channel}&subtype={subtype}	80	554	80	Nền tảng Imou Cloud Platform	/static/assets/providers/imou.png	t	2026-05-16 07:34:13.492807+00	2026-05-16 07:55:04.734113+00
hikvision	Hikvision	https://open.hikvision.com	https://tpp.hikvision.com	rtsp://{user}:{password}@{ip}:{port}/Streaming/Channels/{channel}01	80	554	80	Nền tảng Hikvision Security & Hik-Connect	/static/assets/providers/hikvision.png	t	2026-05-16 07:34:13.502216+00	2026-05-16 07:55:04.741422+00
ezviz	Ezviz	https://open.ezviz.com	https://www.ezviz.com/download/branding-assets	rtsp://{user}:{password}@{ip}:{port}/h264Preview_01_main	80	554	80	Nền tảng Ezviz Smart Home	/static/assets/providers/ezviz.png	t	2026-05-16 07:34:13.507488+00	2026-05-16 07:55:04.747017+00
dahua	Dahua (Pro / DSS)	https://open.dahuasecurity.com	https://www.dahuasecurity.com/developer	rtsp://{user}:{password}@{ip}:{port}/cam/realmonitor?channel={channel}&subtype={subtype}	80	554	80	Nền tảng Dahua Pro SDK & DSS	/static/assets/providers/dahua.png	t	2026-05-16 07:34:13.513755+00	2026-05-16 07:55:04.752558+00
\.


--
-- Data for Name: camera_status_log; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.camera_status_log (id, camera_id, status, event_type, notes, logged_at) FROM stdin;
17	9	stream_down	stream_down	\N	2026-10-07 18:02:56+00
18	9	stream_down	stream_down	\N	2026-10-07 18:02:58+00
19	9	stream_down	stream_down	\N	2026-10-07 18:04:56+00
20	9	stream_down	stream_down	\N	2026-10-07 18:04:59+00
21	9	stream_down	stream_down	\N	2026-10-07 18:07:36+00
22	9	stream_down	stream_down	\N	2026-10-07 18:07:39+00
23	9	stream_up	stream_up	\N	2026-10-07 18:12:38+00
24	9	stream_up	stream_up	\N	2026-10-07 18:12:41+00
25	9	stream_down	stream_down	\N	2026-10-13 04:45:19+00
26	9	stream_down	stream_down	\N	2026-10-15 06:37:28+00
27	9	stream_down	stream_down	\N	2026-10-15 07:17:07+00
28	9	stream_down	stream_down	\N	2026-10-15 07:17:08+00
29	9	stream_down	stream_down	\N	2026-10-15 18:02:16+00
\.


--
-- Data for Name: cameras; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.cameras (id, camera_name, camera_type, imou_device_id, imou_channel_id, rtsp_url, onvif_rtsp_detected, camera_ip, camera_port, provider_keys, site_ids, is_active, is_streaming, created_at, updated_at) FROM stdin;
9	Ranger 2-H3-B063	imou	5552ABJPSFFB063	0	rtsp://admin:bB123456789%40@192.170.43.26:554/cam/realmonitor?channel=1&subtype=1	\N	192.170.43.26	554	{"mk": "bB123456789@"}	\N	t	f	2026-09-19 06:13:01.223405+00	2026-10-07 11:01:18.836948+00
\.


--
-- Data for Name: cameras_asset; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.cameras_asset (id, cam_id, name, ip, mac, serial, model, firmware, rtsp_url, onvif_port, onvif_profiles, created_at, updated_at) FROM stdin;
1	5552abjpsffb063	Ranger 2-H3-B063	\N			IPC-K2E-H3W			\N	\N	2026-05-17 00:50:38.733663+00	2026-09-19 06:05:59.228517+00
\.


--
-- Data for Name: cargo_trips; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.cargo_trips (id, vehicle_id, plate_number, departure_time, return_time, origin, destination, fuel_liters, fuel_price_per_liter, freight_revenue, distance_km, created_at, updated_at) FROM stdin;
1	5	88A-000.24	2026-05-17 00:00:00+00	2026-05-17 00:00:00+00	Tề Lỗ, Yên Lạc, Vĩnh Phúc	\N	\N	\N	0.00	\N	2026-05-17 05:28:45.480943+00	2026-05-17 05:28:45.480943+00
\.


--
-- Data for Name: door_providers; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.door_providers (provider_key, name, connection_type, api_doc_url, config_template, notes, logo_url, is_active, created_at, updated_at) FROM stdin;
austdoor	Austdoor	http	\N	\N	Tập đoàn Austdoor - Cửa cuốn số 1 Việt Nam.	/static/assets/providers/austdoor.png	t	2026-05-16 07:34:13.519712+00	2026-05-16 07:55:04.758563+00
mitadoor	Mitadoor	http	\N	\N	Cửa cuốn nhôm khe thoáng công nghệ Đức.	/static/assets/providers/mitadoor.png	t	2026-05-16 07:34:13.526079+00	2026-05-16 07:55:04.764502+00
titadoor	Titadoor	http	\N	\N	Thương hiệu cửa cuốn Titadoor chuyên nghiệp.	/static/assets/providers/titadoor.png	t	2026-05-16 07:34:13.530852+00	2026-05-16 07:55:04.771497+00
bossdoor	BossDoor	http	\N	\N	Thương hiệu cửa cuốn cao cấp của Tân Trường Sơn Group.	/static/assets/providers/bossdoor.png	t	2026-05-16 07:34:13.535871+00	2026-05-16 07:55:04.778639+00
\.


--
-- Data for Name: inquiries; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.inquiries (id, full_name, phone, company_name, demand, source_page, is_read, note, created_at) FROM stdin;
1	Test User	0912345678	ABC	Khác	home	f	\N	2026-06-09 00:19:17.893796+00
\.


--
-- Data for Name: kyc_documents; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.kyc_documents (id, user_id, doc_type, file_path, status, uploaded_at, reviewed_at, reviewed_by) FROM stdin;
15	1	face_front	C:\\project\\yolov26-nhandien-biensoxe\\data\\faces\\1\\front.jpg	pending	2026-10-12 16:06:57.84933+00	\N	\N
16	1	face_left	C:\\project\\yolov26-nhandien-biensoxe\\data\\faces\\1\\left.jpg	pending	2026-10-12 16:07:25.296692+00	\N	\N
17	1	face_right	C:\\project\\yolov26-nhandien-biensoxe\\data\\faces\\1\\right.jpg	pending	2026-10-12 16:07:45.670276+00	\N	\N
18	1	face_up	C:\\project\\yolov26-nhandien-biensoxe\\data\\faces\\1\\up.jpg	pending	2026-10-12 16:07:53.734902+00	\N	\N
19	1	face_down	C:\\project\\yolov26-nhandien-biensoxe\\data\\faces\\1\\down.jpg	pending	2026-10-12 16:07:59.926353+00	\N	\N
\.


--
-- Data for Name: legal_hold; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.legal_hold (id, file_path, reason, held_by, held_at, released_at) FROM stdin;
\.


--
-- Data for Name: monthly_reports; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.monthly_reports (id, report_month, data, created_at) FROM stdin;
\.


--
-- Data for Name: news; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.news (id, slug, title, category, summary, content, image, publish_date, is_published, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: ocr_training_samples; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.ocr_training_samples (id, plate_image_path, ocr_text, confidence, camera_id, corrected_text, reviewed_by, reviewed_at, status, created_at) FROM stdin;
\.


--
-- Data for Name: pending_plates; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.pending_plates (id, pending_id, event_id, plate_raw, plate_norm, first_seen_utc, status, site_id, image_path, plate_type, confirmed_at_utc, confirmed_by, created_at) FROM stdin;
9	f239d42b	\N	26TA-051.10	26TA05110	2026-10-13 13:44:40.111229+00	pending	\N	C:\\project\\yolov26-nhandien-biensoxe\\data\\snapshots\\webcam_26TA05110_20261013_204440_ed64921510d64fd08ad24a6871268330.jpg	white	\N	\N	2026-10-13 13:44:40.111229+00
10	e96d297e	\N	P5NC	P5NC	2026-10-13 16:04:27.45477+00	pending	\N	C:\\project\\yolov26-nhandien-biensoxe\\data\\snapshots\\webcam_P5NC_20261013_230427_5a162094418b481ba23bf275b58f1ddb.jpg	white	\N	\N	2026-10-13 16:04:27.45477+00
11	e5eea8ff	\N	61956	61956	2026-10-13 16:04:27.775844+00	pending	\N	C:\\project\\yolov26-nhandien-biensoxe\\data\\snapshots\\webcam_61956_20261013_230427_20c7e950d58b4d48bb12d8de4e193785.jpg	white	\N	\N	2026-10-13 16:04:27.775844+00
12	4dcd4d49	\N	SNC	SNC	2026-10-13 16:06:21.823395+00	pending	\N	C:\\project\\yolov26-nhandien-biensoxe\\data\\snapshots\\upload_SNC_20261013_230621_d1ec5fd4ed5b4a35bc7f33edeea4da9d.jpg	white	\N	\N	2026-10-13 16:06:21.823395+00
13	86a25366	\N	160-620.34	16062034	2026-10-13 16:24:46.049158+00	pending	\N	C:\\project\\yolov26-nhandien-biensoxe\\data\\snapshots\\upload_16062034_20261013_232446_8ae495c11c2d4cbdba32f1cb0fcc81b3.jpg	white	\N	\N	2026-10-13 16:24:46.049158+00
14	e63da002	\N	T60-619.56	T6061956	2026-10-13 16:24:46.695872+00	pending	\N	C:\\project\\yolov26-nhandien-biensoxe\\data\\snapshots\\upload_T6061956_20261013_232446_65fdbeb6302d488ba53147c5f10f6153.jpg	white	\N	\N	2026-10-13 16:24:46.695872+00
46	1019dab4	\N	T60-620.34	T6062034	2026-10-14 14:00:07.540545+00	pending	\N	C:\\project\\yolov26-nhandien-biensoxe\\data\\snapshots\\upload_T6062034_20261014_210007_4dc729b3fc3a42a3b8d3e14dd6575b4c.jpg	white	\N	\N	2026-10-14 14:00:07.540545+00
47	cd1330f7	\N	19H-147.98	19H14798	2026-10-15 13:47:14.848742+00	pending	\N	C:\\project\\yolov26-nhandien-biensoxe\\data\\snapshots\\upload_19H14798_20261015_204714_e0dd739b382541589f483eb506743fa4.jpg	yellow	\N	\N	2026-10-15 13:47:14.848742+00
48	287cb8eb	\N	88888	88888	2026-10-15 13:51:10.175878+00	pending	\N	C:\\project\\yolov26-nhandien-biensoxe\\data\\snapshots\\upload_88888_20261015_205110_1dbf2656c5e84e0da35b01e75ea3c331.jpg	red	\N	\N	2026-10-15 13:51:10.175878+00
49	1b46aef3	\N	29H-800.82	29H80082	2026-10-15 13:52:44.494528+00	pending	\N	C:\\project\\yolov26-nhandien-biensoxe\\data\\snapshots\\upload_29H80082_20261015_205244_d0d925b58ad04104b650067a92f63693.jpg	yellow	\N	\N	2026-10-15 13:52:44.494528+00
50	2e6a7cf3	\N	65C-068.76	65C06876	2026-10-15 13:53:42.13491+00	pending	\N	C:\\project\\yolov26-nhandien-biensoxe\\data\\snapshots\\upload_65C06876_20261015_205342_7c7eb2115cfb4edeafcd7e0bdf3e7de7.jpg	blue	\N	\N	2026-10-15 13:53:42.13491+00
51	50aaf1e8	\N	51M-895.96	51M89596	2026-10-15 14:47:56.781194+00	pending	\N	C:\\project\\yolov26-nhandien-biensoxe\\data\\snapshots\\video_51M89596_20261015_214756_602cf1b594f74a5988e4781785cce2e3.jpg	white	\N	\N	2026-10-15 14:47:56.781194+00
52	1caed6a9	\N	291T-800.82	291T80082	2026-10-15 15:18:49.570363+00	pending	\N	C:\\project\\yolov26-nhandien-biensoxe\\data\\snapshots\\webcam_291T80082_20261015_221849_c835903574ff4ae097815d3cc1489314.jpg	yellow	\N	\N	2026-10-15 15:18:49.570363+00
\.


--
-- Data for Name: plate_events; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.plate_events (id, event_time, camera_id, site_id, vehicle_type, plate_number, confidence, image_path, created_at) FROM stdin;
\.


--
-- Data for Name: plate_whitelist; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.plate_whitelist (id, plate_number, list_type, owner_name, added_by, site_ids, note, is_active, created_at, updated_at, fuel_consumption) FROM stdin;
5	88A-000.24	white	Administrator	admin	[3]	API TRANSACTION	t	2026-05-17 05:27:06.881865+00	2026-06-15 23:42:12.26964+00	500.00
\.


--
-- Data for Name: projects; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.projects (id, name, location, area, height, bays, load_capacity, image, description, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: roles; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.roles (id, role_name, allowed_menus, created_at) FROM stdin;
1	Admin	*	2026-05-16 07:34:13.832404+00
\.


--
-- Data for Name: rolling_doors; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.rolling_doors (id, name, supplier_key, site_id, relay_type, relay_config, is_active, camera_id, auto_close_enabled, auto_close_delay_sec, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: sites; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.sites (id, site_name, address, description, total_slots, is_active, created_at, updated_at) FROM stdin;
3	Lán	Viet Nam, Viet Nam, Ha Noi		0	t	2026-05-17 01:41:18.24519+00	2026-05-17 01:41:18.24519+00
\.


--
-- Data for Name: stranger_logs; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.stranger_logs (id, camera_id, similarity, image_path, site_id, face_embedding, status, direction, converted_user_id, note, event_time, seen_count, last_seen) FROM stdin;
172	webcam	0	C:\\project\\yolov26-nhandien-biensoxe\\data\\snapshots\\face_webcam_20261012_233137_65b4655dd32946f68be80bbb21f6a262.jpg	\N	\\x20971bbd270bd4bcd4781cb9995333bd4f56613c39776ebd38ab493dc4a7413c2251003d79880d3c807593bd3b019bbdfa11523bfdb8f13de95d2dbd8c2bebbc4ee00ebd12441b3c2248373c7f7831baee27d13caab085bd388990bdafa287bd46007a3da36a6e3d896263bccb42b93dcef6933c409498bc39ee933df620af3d01f0823ce059aa3d9c9ba9bc509a12bd271e26bda3b926bc5c0c56bd638db63da41efcbd471470bc2a16a63df20a2abd4731033cd5d968bdb277ddbc3b9a9e3c41ae6a3d73bb483cbf505f3cb31aa0bbd3bcb7bce494f7bcef1eaa3b9345073d3d37b13c7973983cc026203ce0f0a43cc178793d782b54bc127d08bdc1f0dbbbb43befbc90f61ebd97cc813d1e015abd0b2246bd50374dbd3f5317bd73ed9d3db5126cbd2dcda4bc597ff7bc8e6ea3bd41418e3c31d7003d5e8a5dbddd5e393ba2f133bd478f5a3d050e45bd803427bdb7e751bdfbfe33bde891c6bcc84abfbcf88e263d3b01f23caa569bbc9b27dfbbd884283d76d98cbddb88a43de31d843c973e0b3dc8a0c2bc0176f43be1c2943c6f2dcebc6d3f3b3cc2da39bdfaa69cbc78ab6c3caa112e3d2a67ec3c402fb93c5421c63ab37bfcbc037b933bc87f60bd06c594bdc567593d81f491bd95a5453d51dce5bc8b0101bd083da73d854e693d0d32063d7ee4d4b8f9f06dbc0a1f1e3d8a2a993d6c0ebdbc0c88d03c6c5745bc6f9f623d07bf8dbdcc2047bd2c0dcdbb02b71e3db33c353d36256a3db7fa54bd1cb7c33c574f16bb7c3aa63dc81a523d16343e3de0274e3d41ec423d455307bdff6121bd4cb4cd3ce95f563d6165ebb9b257b93c6dcea73c2ccb90bcd259f1bb64742b3c701dd93c0bea6bbb289b5f3ce51e5ebd1413bd3c836130bd9d26a33de1a376bcd1f398bc5721f4bc81dd893cd6046d3dc51bffbc7adbed3dcab9953bab80423d7298aabda28703bdb1728e3d6f4a22bdfb1e803d6abe59bdfb555c3dddac04bd16ae863d8719273c3c866f3c4a2f8cbd5ba8043d9be58abcd2cb14bd353e483c40fca13dbfe512bcdf0708394818d93c4f31393d8f17393cd470733de50138bda2fbd6bdb904f53b7a333d3d8fd2c5bc5db53ebc1ffd5e3cfa07e7bc5aac243d078c383d0ae04d3c06e5e9ba136a58bd95c13abd88d97ebcd3bd943d079b8abd5a326d3d9c7da0bc0ddec6bb815e373da0febe3c1f8157bd28bac03c6db8473c40df243b7a1807bd7ea202bd9a67383d6573d63d69f47dbbb6f401bd5d2fa6bdc065cebb94882b3e81dc373dc87a983d315a5dbb1f5f3abbbb2a4dbc35ea87bb9b0c4b3da50d263d43f6c93d73909abcddd4b2bdd9df5b3d799d5e3d8628303cebfe02bd774f9bbcf10f653d522e4d3de388aa3dc95b1dbdceef81bcc951193dc0b504b9fe82cf3c90fce13b3d86c3bc2e49bd3cf6da6d3dd5f1033d95e58f3d8e8969bc23daaa3d2b10563bcff652bddfbcc63c490973bdf283e03c4448183d00cd95bc0e6490bcd9f5083d7654663dc0d665bd472cfd3aa913a9bd1b6668bce699bdbcac47b3bb402c4abd0857553d2da0b9bad458ccbcb3f95d3d1bf36fbcd02855bcb5a9393d80f510bd25b921ba9db26abd1c55fbbca5fa7cbc9821d9bc5549123d9433a0bb584158ba0c16f73ccb383abc1f3b6c3d4ca147bc723eb73c181672bc840566bced63f73c865b803c22ca81ba4dbaee3c5780dbbc0f386ebde4a5433d65b84bbc10c02cbcc00d113dda2af4bce8b4a2bdc610303ce90ec33dc48a6ebd6032903c2772843d1894973a87eaa2bd17319abdefd107bdf7fd91bcdf400c3d4b8a813c552334bb4cf2be3d6128153def7877bdcaf40cbd2a8ecf3cbdc3a93dc0b80ebdef09ba3d2ed7bb3ba643a1bcd8ec8f3cb6d30c3c914a0cbdade55dbd3810da3cae6cb6bdb8329a3d0c6323bd3591183db848733b522c25bcc19cb43ca74f23bd527e543d4899a93c11c3a83ccf2b163d11f71a3daa0749bc961594bca8b469bdc904853c436db6bbbfe94a3df0b5fe3c58abdfbb866e1dbba944eb3b324622bbbba1a33b349c9c3db55430bc5db13a3d760173bcd0f916bdb2d1163af31006bcac31d7bce36613bd33eb333dbd8d9f3c2eb4f4bd155e9cbd180881bd8fbcf9bb779410bde472973c1a4665bb0fbc66bcc432d8bc13d341bc53cf9ebc136dde3b8279fdbcd4a648bd57b4303b4372b83c474713bd33c97d3d7ce8c3bd519a33bc95359d3da32377bc4bdbf7bcfd60e53c4fb115bb0d1df1bca07b803b38d551bdc47e1fbcce583c3cf62063bc37263abde15e3e3d1d744f3d8ad50d3dbf94023dbe2b9abdd1b25abcab1d433d1e038fbd15ec1cbd2c5722bd6337f53b51bbf03cce5da33dd6ae65bd9a4e9f3ddba9bdbd2a50093d71e35b3d43e748bc4c56d7bc21ccaf3c57a9273df6441f3be87013bc4d8ec43cfaf3933d7eb6cf3bf0a412bc50c13bbde0a9c3bca7f8443d88fa87bcbeedd4bb918383bc8bfeb53b6070133c07bd303d7f4d8d3be44a96bd6554c3bc22bad53bb6dd7bbd9f17a33dfbd61c3c86008ebc7798753d1322d7bbe53180bb3b7b90bdcca28ebd87becdbcc1790ebd7f1b613cf1727abc2267acbd28ed3dbdf5c83fbd8da348bdec1ee03dab6bb4bc48e456bdbc6a73bc0b87f9bca2d32bbc05d2223d062813bdec5eb03cecd727bc91f00c3da498d13cc41740bb603eb93c10c031bc6623933d8512e8bb80a0c7bc3c25f63baed6943db9cd333c7ef7f5bcadc752bda4f8b23c96d8113d376385bd6862833da6cd013c6b6d85bdd6968bbcc9827bbdc95ce43bca8a3bbd8192a5bd22b98d3d349e9a3dc1cb8b3ded95b73c337d103deb3f6abc5ee2e1bc52061d3da294d9bc5c00a63a33a50e3df31ff9bc38abbabc	new	frontal	\N	\N	2026-10-12 16:31:37.312395+00	1	2026-10-12 16:31:37.312395+00
173	webcam	0	C:\\project\\yolov26-nhandien-biensoxe\\data\\snapshots\\face_webcam_20261012_233433_193881c6e7db41a996f2b15efe18e1e6.jpg	\N	\\xab0619bde0f106bd9f483cbdfc4b3fbd224045bc3dba12bd62758b3d0268a63bfce82d3b07a0dabcef0b2a3b6e6896bd78f6693cff037c3c8c3b61bbf219a2bd0acad53c6e6e4cbdbc54ccbc7a5390bd13f30dbd68d05f3bf2f1a4bd0370013daa257c3d56ab67bd2a8656bdea9cae3cc8b1f13bda7a6b3d30149dbc6905533df36101bdbb7034bd2cefa13c2c4de6bd672141bc8857c93cdfeaeabade2bd63cb0c1a1ba0c9d8fbc8597833d16fd1cbd19a9d8ba820f1b3d44edc1bbf2c118bdab48283d2b3fd2bc669d243ba5f84bbd264391bc56da023d3899763d09f0d63c3e1978bdf7c51cbd728c663da6ae86bbaf3608bdc89332bd94c98cbd7283593db58c1e3dfed7e6bcf261683d8e0c09bd9de193bcbb2c903bbd3765bc64f724bc106d0ebd999d82bd91d425bd34234dbba10962bd343586bd4888913d876b4c3dd48aaf3c24bcb43ce1b7aabc8d5582bd0b284abd1a04963d439436bb5ff1b1bbeb135c3cfb92f23c3a4c44bdfacc573dadeea1bad254a73c19918dbd2ffdbcbb6270793d2c933dbdcb0a0139032c873c3414073d597ec43cbbd692bd670bd83bc39b583d3113103ddb15903d995b103dcd39cfbc4cd351bd1a8e1d3b1026d83c14fab53c316198bd204ce3bde2e1b73bfb78aebc6c271fbd996da0bc35360f3d376a3dbd823c643ded3b723ad005d0bb845e25bd144b4f3d3bf3e43c3a8c7c3d2ed1bdbc0060843cfb5aa4bcfc8bcf3cfc2917bdf8e2953c8489423cbd8fb7bd043ca9bbfbef84bbf47ab33cbeb4193ce4e593bcca4396bb84491dbc4a7198bd34f788bb8ee90b3d2d9b113dc3d5003de20ee63b39536bbd1b743bbcfba496bb922457bd4f52903c3aa2273da78031bc4d9e103df46025bd20ff82ba328f41bb886525bd406e63bdf01b76bce75da23cb2b785bd528ceab935eae63b6da679bb131311bd3449043b336f1abce22c04bdac2f6e3d701934bcc565ac3cd36e09bc4f147e3d7b92903c5037813c1477023d54bfa23bbfcc4d3d501ba7bc5c87053bfa471f3dff2ad93da40e0d3d0d8e963cc526c0bd49ee983c32c11e3d70d5ab3d54f6cbbc68834fbcb5098b3d33a0a03d84a829bd1bd5cebd51c7643dbe0dfebcc605093d964f5fbb026b2bbda8918a3dd99598bca0a8bb3ae7ac5bbc67ec753d4cbf86bddbe2063d516d80bb62b000bc56a1af3c12541dbdf70a33bd64882b3cc4e66a3d8417d0bc0d6485bc97cde53cf994393d363067bc7db23f3d37fdcabc606f913c59f777bd0630793dd4ad583cf1d4fbbd2a42683cc51abfbd2eeccbbcbc4229bcbeef083dca977a3c3c31c63bf29f17bd6c73903d0583103d9ab795bd830ac03d350608bb8826f6bcbd2987bd60ad49bd052fbf3c546122bd3933573c1a20a93d6d31993ac604b63b950486bd7b49113d9f618e3c7f918d3cd05a573cc2b9fa3b405dbabd0c25113c6467883cbc8212bd919982bd61d486ba4069823cabbd273d69fa3abd8588743d13ff6f3be033f8bbc27a83bd023675bd0b21d6bd1ab3e2bc92e9a5bd72a7373d550aca3c96a597bbe80a88bd64c054bdfda0a63cd22ea13b027eb73d1cd99f3b0345a6bc053a2f3d3c7a893d176c293cd487dd3c8fc3c5bba65fcf3c767bcdbdb510313cff8ff4ba52aa5abd23f0d73c078e8f3d6b79e4ba41fc31bdc76d64bd9b27f33c26ea723c8215cdbc7720c7bdf407c83c5baa4d3dd596423d42725e3c17a50e3e4dd93c3d254948bd7cf42e3b670ac5bb35684f3d03a6f5bd8ec740bd0e87da3c590f0ebddb097abd926f3d3dc15e3bbabdabafbdb5029c3cc401623d4d173d3d99312b3df329bbbc3702f33cc757bfbd3281bd3b6f822f3cd1dcfabca1592d3c0273b5bc823a7bbd821b23bd2760d13cbdcf9a3d643241bde0d8933bffc4873c1ff717bd3bd58cbdb043703db166a63cdfbe54bd6e13d63c019765bdd2a04cbda04fd1bc7e53ef3c3cbe283a6c5bdcbcc6caf3bcc8573c3d96153fbd9e3b53bd41c3073acb139fbc1e41ff3bd1ac1c3d0d50a43cda0024bd2b9c0abd88c28a3d9421923dfe825c3d83ab5d3d590c6a3d1638413bfe2e44bd5324c0bc594529bd69f89ebc2aa5143d1aafba3cccf915bd51970fbdd06c23bb4b1d8dbc2a799e3ccf1a91bc76f936bd872f8abc33f5e03c5e3e6e3ddf630bbd5d6155bd2472823de9e4acbc86412d3df3b13a3c7e7e0fbc2e0f2ebd92ce3fbdc96b253df760db3cd9d1173df0da43bdc857963d4d7769bcd91f5cbd95e4293d59b0f93ba85bda3cf0590dbc09c8083d981aa83c321b5b3de0056abd4eaf513caf16d7bc64913bbdb7dba9bd1b7c2a3d1f8bd83b5df7b4bdcdd573bda1c9c7bce368633c9fd2803deef210bd08ed8c3dd620d1bdb58c313d9d1a55bd4ca6fa3b623bcfbcd83339bcc6c3b1bca1e4a43dea1a85bb660889bcfe400abc54c3d7bc93d51ebc7b7a7bbcadfed7bde3c67bbcb4ae32bce37c903ca9fd65bd3bd78e3dc95f8a3d1ba7993d3ea15fbdf9410fbc6922983d8483b23cd0df943c4ae10e3cbaa673bb67d23c3d9b39d3bb3c56d13d3f6f0b3de5dd82bca234bd3ce3ac09bd94c3d63ce7774a3ca8a7913c89f7523dd67d85bc74d51a3df10d12bc152d64bcaec24e3d9e1b053d5ec80a3dab7b89bdaeb920bda2d0ae3db8bb483d2f5c9bbc13c31abc53e17c3d3d400b3d3b8bc53c60c0ccbd79673abbdf1ba0bc5c2d833d367b3a3a566351bb9cdb373d3ea253bd0cebff3c727e19bd4793483d21c1b03d89ba2bbd25d5abbdb433e03cbe4da23c7c39d53cf2a537bda9c7493d3ea8d3bcf0b9e4bc916b953c82e4a43d1d75a73d1b8917bd7029043d8e78413c1fe6843c06a8743de9769ebdcca522bd0a17253d62d2753d7067763c	new	frontal	\N	\N	2026-10-12 16:34:33.761952+00	1	2026-10-12 16:34:33.761952+00
171	webcam	0	C:\\project\\yolov26-nhandien-biensoxe\\data\\snapshots\\face_webcam_20261012_231835_d881994a5b744d1c86716b3eeff4de7e.jpg	\N	\\x0f6881bd762a10bc7f279cbdc47162bbd880143e8bea34bd7228233c1f96763cf5c812bb15f1fe3bb7238b3c8c6397bd3b120bbd7f5ca13c1f48223cee90c53b81c567bd1ab9a03dcdb6953d0f412d3c102787bc0d98f1bb26e085bd3070adbceb67c03ce165e93dc2e5c4bcc456253d52140c3d111cda3ce001cbbcbed68f3cd79a873ca45e463b4ab38dbaa2ff48bdabd391bd11a6993dcc8c313d91d18ebc56ff27bc5984353b749832bc1cd2c63d74e3863d44e2653c3ba138bd710c913dbc93413db7c9a0bd4d0e76bdcab3703d4bf31e3c8a69d73c23e63f3ddc8f963d1ffef8bcb60b4f3d492c2f3dafeaba3be18aeabc5fed31bd5e6b343b46dc45bdb0f53cbccb5402bcfa99853d1dc5163d7defeb3caf8043bdece99fbab801333d3e1ba83c776d30bb38a6a7bb680a1dbdbc6a383c0138483b485117bc1f9e66bdd3213ebd72ed6c3dcc423d3dfb06f43c7aa807bdcb44cfbc133a4d3d9a8c2d3cf329f23c1a66953d6432a83c770e0c3d1e10833dbe4cdebb6aa780bb86ad1cbdb39b5fbc2a74133db6d8f4bc57ca27bddfc888bd6c25b03ba253113dacb657bc133ba7bbd68fdb3c7372133d60af13bddbb30cbb305391bdb12e74bd2041b7bc0a14e4bdc0bc8b3dea17293da54d29bdf9a3383c6f6bf1bd27e3923d5704093d966e8e3d3919b53c1d8c163dc876a43d5dadb53d35280f3bb1bdf13cee60833c6f05833c3a9c9fbd578952bc700909bdc2523b3bec59b3bb9e16073d35689c3d446d373d903c0bbcc1003c3db07f75bcbd89333d3207823d84cf33bcd1eaa13c0509a23cbac1d13c20b59c3c52c6d43c92ff933de5d1313d74f1513d07ece03c5cfc2d3c8a2013bdaa9a683dde8d3fbd94e82b3d24a3b4bc0481a5bca601af3ca94b06bdac770c3de59177bda8a806bdbbe3e53cf93d19bd4e09aa3d289ef83a52f4e73d6cfb243df46b87bc4874d93c6a537b3c0cc9bb3d9618823c11b408bd05a5bfbca563a23b31084fbd6fe5e93c887838bb9fe1c63b2a650abd4fd4f1bc4848833bef92ad3c12e1663db55208bdc920073d8bc2af3c15a1843c8c1c9bbd84a0b1bd0e7e063b01272a3c0358a23dc62b34bbaf924dbc4c228cbcdbe76b3ddb6afbbbdc7c0dbd19a25f3dae262fbd33ecddbbe257a43c0bb38e3aa995abbce4d2ecb9d04937bd22d0e73c1488573c3e41023ee7e80c3c5abc09bd9e71873b009a1cbd7cffb63d0f2bb43d3696523d7eb32a3de502503c5444483ca8cfff3b99590cbc2a37973d3354833d83a9c2bc939f953c72538f3d9f8b423cdd0283bd8882d83cdd37313b98663e3d6ec14e3de8421fbddbda1bbd6e5111bdfda5eb3ad6dd843be959e0bc77d4303df21b923d713834bc57bae9bc5ee382bd8c4d973b8dffa63cbf84023da1d815bc92c8aebccc7e403d64de04bc1a97a7bcfe48c0bd49a6223da78ddd3c1c3768bcd9c0a3bb7b2bdd3bb0063a3dfc5054bceb1ebabc8235723c6df829bdcb3510bd9b82643c041d693dabd6173db31f593cc701573d6222b1bb3442adbd96d7033df086d13b9f069d3d68a206bd4a2019bc7ae7243db1e672bd494b9dbd6b7234bcefc10abd4706733c60318dbd8671a2bc993993bca4d0a73a5298143dbaa4c73cae0696bcb0d18e3c36c0963b01eb3a3dbb3c0dbc627587bd0bdf683ddbacf63c4dab943ccbc47fbd69e95f3dc8f2133cd7a97a3c0c6987bdaa429a3c122c923de205d2bcdf48c7bc80a2063df4f466bd0c2ee43cb86be3bc147ab3ba0eb7a43ca0160c3d634e61bc9b5b85bde7d8013d574b1cbcdc9886bd3fbbcc3c89928c3d2370eb3afb63403b13bfbc3c5c0da73c37c6713c164571bbced5553d151a38bdbeae123d9de5353d86a408bcb372b63d9bee7c3d0f30c5bc098dbbbc8f0cffbc513346bd4a95a13daa6b4d3c4213323d878bc63c17ec9abd59bacfbb0607b4bcc4ce773d48077bbc7816ee3cd8235f3d7fc0083daf29073d6921dbbdccd5e9bb5c750bbde1f450bdd8bb813d0a7501bd891ec23cd199103d02d589bbbbc0ee3b83ef39bca99e8e3cd066853c84e9c1bd3156fb3c2499e8bb8b43513d1c2923bdd7f33ebd50b700bd0528e73c7e06a13c57f72abc63fb3fbd15bdb0bd22c4193d32d80b3d7a4d96bc4d3a0e3d3c945d3de9e161bd7acb8fbcc6b5a0bc8a6e0ebd8b0005bce424d1bc5cda2a3d48fbdd3c582ef43c2a6c8bbc8a6d9abc93bfaa3da14acbbc946e8c3d5ea4bd3d86ec5bbc3c37f9bcb828aebd75ef023c5fde0abde96943bd9606e1bc13098dbd5e69583de5a3a93dd04f433d7c5303bdba0a9dbd1f2c023c246b44bd1ffa8d3cca5ff8bc619686bc47675fbb00dfd93cacbd523ced06d03bdde45cbd565beb38b39b6039422eb33dbc49fa3d358517bd69a4cb3cbc51543cdcf9b43c80d8e6bcb10814bdad68ae3d6aad063d1b5fc6bcd33e203b768348bdc073493cc74a233b8be1ce3b834bddbcf072b73d25b4ab3db2d985bd226c3c3bec102bba6131e2bb5fe46bbd0f481b3df8a24b3c177b153d5e837cbd9f23a83cb0e0e3bc8895cebd06c013bd7efd333d52fe98bd4ee421bd58e612bd917878bdda7106bc538edbbdd6c5c33cb476e3bc2b02833cd2298a3d401874bcbd190abdf005a8bd9ac8e2bcc3a588bdb820a83de921713ded4e2cbd831f373c81005fbda82e0c3dad5ecfbcdb0982b90b591a3dcc0a9a3c8279483d76536f3d43d0ebbbab6833bdedff83bc9dd5c2bd7546a63a64c33dbbd9fa1a3d94876dbd365e233d4fd573bdb9830d3d519388bbcab497bbd13d893b9bd485bc0d4c59ba09ff40bd02fa63bbe31d9d3d78aecd3dfd04a93c3afeb8bc7f14163de0efd93c7a0fbdbb51126fbdd064adbac97ca33d1e63de3b	new	frontal	\N	\N	2026-10-12 16:18:35.892211+00	20	2026-10-12 16:40:22.427499+00
175	webcam	0	C:\\project\\yolov26-nhandien-biensoxe\\data\\snapshots\\face_webcam_20261014_215418_6eac0dd2dc1d4d45a86dfabac3ec648e.jpg	\N	\\x347229bdabe337bd5739273dcf09ecbc1ad2f73dee8b4cbcb122173c846cfa3cad19483cba3729bcec09f63bdf55133df33ba6bc0b51d33db3e8a43d4a0075bbf71953bb7b5ebabc64ebf13ca5d7263d6440883bca5043bcb10f97bc6cf9783d13f563bcf625133d3278f3bc09f6123d6044353d8e3e0e3d4aba1d3de02f0dbb1c972ebc4d95a9bc99ae72bbd53bf6bc4fcc053d24cc68bd86c8e6bcd50319bdbe848c39ef9172bd76b8d1bcb3d0023ef5fe953c4053e0bb8d3119bd7893363d2e4f6fbcfe5e853c533c95bb8d61c83cd98d4ebd873a023d673bd23c03b68d3dc69e81bb45f6f93cacf990bca6229b3d7eba51bd1ba059bde427fcbb1722783d101aa03b777f05bd6b3a553d8fafedbca784af3c598d843db6cf00bdeca10bbd59f4033dfc5a9d3aebce8b3c9f99b4bb9f8b103dfb667abc9b3c26bddffa75bd92ac43bd0e4857bd8f82f23b79666bbd5ca4c5bc8587dd3cfa277f3d5733063d9f9526bdd3e08d3ddccb003dd8c63a3da3a7acbb9d039fbd087caa3cefa01dbd68b9853d3eee0e3db24224bc015cdabc322aa2bb53a2683d76c9e0bc592d9ebb59f932bdce2d983d95463b3d519050bd8e1a07bc4bea9bbd569d6dbb8bbba1bd2c720b3dede573bc180a543de46a9dbc40a4aebb65bd37bd86bd193cc92a263da5cf133d45d7df3cd9eb5839ffe0a3bc0352463d9bf5da3cbecd13bd755f2cbcb77781bd7ae1243da9e507bd63cd5b3c9326e63b2f8b893dddd8143de8dc90bca918153dc283f83c2b7ac3bd4cd5ddbc711e83bd9ad808bcd47e593dba89cc3c20f9acbd70847dbd65fa573deccd1dbc2bde933d0c084cbc71bde73b16887dbd52a48a3c8546183b73544f3d5727dbbc045608bd6a882abd9a780dbdf7f302bdc0e6d3bc8f9be2bc8a8effbbc324b63c050d1dbda0ffe93d5580c33d9a80a73c943ef53d8a36073dd80197bd052ef2bc059f4ebd00b8113d07f4f53c4aef05bdefaf083d9662113e94f6353dba41013d0a9e82bc00e9cabc95a8f93c58ef243c9059b5bcce96353d1ddddbbcc15664bdd30fc2bc386c1b3c6a28ba3d0c31203d39c7d2bd809377bd73f8333d6930b33b6f37e4bcee6b0cbce2bf373d3412f6bd89258e3b5dc000bd4393d43b00dfa13c17b220bddeae0f3dfcdf52bc0e456fbc10a94abde31650bcf9fb3bbd7c11903d71a6d6bdc5d1df3c444f4ebd11026d3d1724d63c66a7463d2c1d96bc8a5295bd5908afbb991e5f3d46b62bbdce7dc43cb81a33bc70d43cbdcba6b23dc31410bd20dd2ebc70b2753bc4424db70f699b3c0f15cb3d33fae93bfde084bb57488cbcc11affbc368a1e3d7a8936bd7625e33c17ca3e3daf9d4b3d2b75633d3c19ba3ce090c0bda697c43ce29e26bdb6fc6f3d843204bdc4f7003c2289493c443e2b3dafb3cebc244e8bbc758b18bdea6d223d6a8d1e3db84bb93bf1f1553cf059b63d84b8b13d0faaaebc981b153ba17dd13c8fb0913cbae2313d0375723dc294573dd8cb2f3da911063d54a721bda6310ebb55e77b3c9ffaccbcd800de3ba83da0bca9f3a23cfa0c9b39e86ad73a3fb8aabb78f78ebdb605623d346729bc1ee61dbbe754ab3ca61d40bd70a321bd295bc9bc0747a13dd79d603c2182963cb96bd03cdc60b8bc0976a83c99beae3adef509bed9d027bd6c8d7b3dc15281bb27a73f3c48acefbb583fc9bcb221a3bc131115bc02ebd3bd0ca5b1bc3937ac3cd843453d8bf5bebdf50302bce6a039bd1547823b59d04fbd6b5e95bd5c99f43cef7f6ebc8b2378bb4b6241bd78e8bb3cc44158bd6b7707bdad508c3d73e1d03d1d1e5c3d33ed0e3c4431e9bc0130833d0e2450bdae9ac2bcda0da5bc214cabbca208ba3b0404673dab6f9d3dca9224bd820708bce6cd41bd7dc8c0bcc8e7fc3c50ffeb3cecefdf3c6364a73cbcf85d3d8e3a583c07b607bd4c273ebd3f4f973d66140b3d0ea4b6bc7f7a0a3c94f9b6bcc2f7c43bd55f0ebc01c32c3c11f2133c2b6be8bcd6a8cdbcd418093df19d14bca68c373d12e62abd2e8914bc372aeab8df71f5bc5e05573d2a32063d7955f3bc81328c3c1bc158bd2cc6853c89d886bc1de78abd79f1d7bc20c7203c4e0f8dbc3c25acbdc37f6bbc07c9183bea900ebdaaf1793cd19b8a3c8431e63cd1df143dda0a4abd991e293cb6e28dbcfb136bbc0d56f33ba98799bc691b1aba6bbb4ebb42ba013bedecb1bdbae09c3d7cc1523d44cd81ba0b84f03b335a4c3b1aaef6bb36bca13db5a09e3c6a4211bc5d4685bc1237bb3d43a485bc5eb83cbdde73bc3d57fd223d046dbe3d4fba303df34b133be1e7703dadcbefbcc690573d8e059abd56fe6b3dc69815bd73b574bcc20f99bdf76dc53df6cf9bbc9bba7bbca4fa973d955beabbf7e2673dc17e9abdde663d3d1ab2483d24cd8dbd88f388bd0347ebbc85ff153c1e64d53c4c9213bd3aa01ebc9c50a8bdf3be853c2a18003dcb27f2bc0076813cd298b13d36af0abdf3c45ebd05ba2ebd2f192c3d7e597cbd02aed93c8a8d933c5b91e43c9169babb3a97523ce857c6bce3c1903b8e6845bba647503d85aafebb097880bb5168413c0bfe10bd64549bbd9436b43cdff78fbd85a6293ce41c443cc578b7bc7ad7bf3ca3aceabc4c99ffbc00a745bc3a396ebc27ff18bded6aa63c5cab0e3d411792bdfb4f933d90dfb83c41ab1b3dde05023d8bace23c0e1b83bd15c7f2bc7050e2bc96c1003d63b4533dda15ad3be5af9fbc565195bcea66423dc980c6bd59e2323d3260f9bc42c96abd9495b9bdbf0588bb5841903de5440fbb169f78bcc7ed78bd65e6503d447db0bdbf04853bf9924f3d21a7c43c2c2c9e3d0a5bf13c2c73b8bb2688ad3daf99fd3cd50acb3d6ec53d3bfd9b68bda0972a3d	new	frontal	\N	\N	2026-10-14 14:54:18.764744+00	1	2026-10-14 14:54:18.764744+00
176	webcam	0	C:\\project\\yolov26-nhandien-biensoxe\\data\\snapshots\\face_webcam_20261014_215419_c0e397fa10a7418aabedb145d41bc968.jpg	\N	\\x7b6a0ebdd270c0bd5f0170bc8a63a9bcb85d9f3df3d9913c6e47f63c60eb0abcc0ad1d3db522c93cca7c20bd3b61c03d846787bd0b67d03d7c6297bd2be1c6bce1534c3c2876e8bc87afd13b8d632b3d4c906d3d6b7ed73a110469bd51f2533d6b90a43b3ff51f3d6bfa5cbd23301dbdd5ce2e3c2ac05d3d975b683d0e61853d1e400c3dd1b8503d3f0fc7bcf4e96bbd11a81abcc245debccb8642bd017a5b3ce37e8e3db0a39dbcaa688b3dc51f663db2ec8f3c3a56bd3cb49e75bc23c239bc708635bd1ecdbabca3cfafbb7375f83c163e34bd960c623d2884363dfa4dbf3cbce5bbbcdcab803b5edebd3daa563e3de60dc2bb50ccc7bcc3118fbc1bd7353d8f16eebbae7397bdcc51713d0aed2fbc371b98bd8a37613dee0e5cbdcec3663d4334513d026c6dbc7305a9bd0ff6053c8a4f9d3d5d6da2bbc47b673d735c9ebc5a65803d2a7a8e3b8d0d44bd68b3f6bc488210bd8dea8abc40e82bbb310032bb5255aabb76fa5d3b228d863df459003d17ce503d97a474bd49d4393c32e8543d9388c83cda0c4f3dc956b03bf8da673af0bda4bc3e49253d63e9573db1ea35bd33699bba2064953dd278cc3c592e62bd5ab38dbdf3d32abd245418bd29c92dbd90c990bcf7b1323debfaf1bc01f41abd1a7f83bc83e472bd2bff9d3d5cbbba3d112d023dfee2bdbca2f0343dbbddb1bc104f923c5e9cec3c620f013d754c0fbd9153c4bc343615bd53baedbc4033e43dacaa56bc43961f3dd7723e3d7a55623b6328573dac76833d9397ffbd2dd887bc3ac1babd9d484fbc8134833a0db5f6bcfb065abd16e9083c0373363d352306bdd68dbe3b4ad74a3dd26780bc40f2b3bce89d8f3d63e515bd1e89c1bc96f07c3c9e323b3dadbe7abd9a4b103dfbf64cbd5598a3bc9e9ffcbcae4c2dbdeaea71bdcf1593bd4c50d33d4a87603db30736bd1f78283d4a2914bc61f6fabcdfb1193cdfdd3f3c52d0ec3dfd518f3cb2d7983d576a9e3de1e8403d70a50abd6549993c8edd5c3cc760163c3b2d513d08bc96bd43923b3d951ff63c7cfc983b4c3930bd3f3debbc5984a93d7d698fbc283a54bca3a161bd0d445fbbcb09983dff14dbb8e390f7bc562fe6bcd4031c3d9b8bf2bd06bd15bda6e9a63c8f2723bc60089d3ccb32973b8eebdd3dae141cbd82dec03c32085cbb168e84bb70200b3cac554c3d500d53bdbf06d33bc1cfa7bd63aec73b6ac92cbd768e8b3caafab1bc0d0a14bd0aa50d3df7e2543cc77b9b3d7d505c3de83a23bd5c0f57bd1265253d78e3b9bcf1dc403a2fa79f3cd3b4dbbc2f6c1abb2f52cdbb5c87623df62c5d3caede34bc9b0296bbd1e915bcb5db183d27bc9c3d667f043df594ccbb35c2b2bc97d3aabb97b26ebdf9250e3db636a03c8f47043a54cbf0bd008167bd579d873d236e96bb5f470ebcd04619bc4e884fbc3a26723c5cd0f13cc60a2ebd108afcbc32c4d63c9513e53c7f594b3c6fafbdbbc95b43bb791a963d2778573c9019e03caccb283c35791cbd5a6d2a3d4ad93cbd28f685bdcf2202bd03f8bbba48e4ac3b6fe6213d52b273bdba9acfbca7f35cbd5c775f3be8c3c8bc8ac25bbc04658fbc6d394ebdb9a056bdbea99a3b9584b13cb80c0cbccc83b53dd2a4053d57cc113b0bbb563d224fa8bcce3dbabb41bf3ebda92601bd968886bce8dabb3be0ede43b89572fbd97a92dbd543d5f3cf965bdbb897127bd72c21fbd0fea473d7bb40c3cd77b163c241eb9bc1cbd4c3ce1610ebd6f8e813cbb4a76bc16ab59bda71321bd3cf25a3d09d31b3cc49cd63b87e5a23c3996c73cef29a43cd0911a3d5d4d803dad6001bd75af0f3de3bd4c3d67cd55bda7645dbd4f648a3b47471cbc371e1bbd7a1302bdaf2f11bb7a9bcb3dc60a06bd50b5e23cea93f5bcaae22bbca75d513d263a213c0e35833dfb4f1abd9ac8c5bc8793033db824943ce721063b3c1df0bc42fd25bc1b5b243d7ce29d3c81ca20bdbd966bbdbb7a9f3cfbd6fbbd44f9b23d42da6b3ddbb61fbc395bc73dbd3734bc4677473c72c93f3c74d0553b7a34073b79e23ebdda0b5a3cdf0ba3ba5d6571bbef829e3c8d32c53c96cfe93d5446bebc5b3387bde017a0bdb70797bc3c3d753c1b0719bd2b5e2cba0923233d4d6528bd1c52133d1b104fbd2212e33c076f9b3d822693bc62c7703d91bb973da6b89b3c3799fe3b43ee3cbdce3cb3bb1a8e783bcb68f3bb834491bd5ca1ffbae934c1bd49ae6d3c4cfb1dbb2d9418bd005ce0bbaf0d42bd201a4dbd99fc9fbd326c93bdb39ff43dda949a3d0a45353d96b3463ddf4e9bbd5f589b3c8273133ddf1d363d8d43073dcf2d33bc975b3a3d65a121bc8310edbc6965483dd547843cb60f3fbddbd6b93d0ec4783c8d4962bc3097043c9034133d82a1173d5a57bebd7c44cfbc19c7b5bc22a1353cf13f4cbd2ba00cbd698485bc30e513bbfb07033d2e89b53c85e60cbdedd01a3d413afdbce8a0953b323a14bc49c1913d4cfdc6bd2b154c3b552d1f3ca03ef03c17119dbd99f6c83c3fd413bdc375b53bec4176bc72a7a1bb544856bdc0f17a3d5731a5bc4527853d0a959f3a557692bc3542413dd32da13cc5c5dfbc64bfe9bc44b881bdc9ffd1bb3d3495bc432c1c3c5a1c883c449f94bd7d1389bde0feca3c0e32e8bd4e09cbbce92524bc6f1904bdb6cf0bbbee880e3d11b71dbda6b1fc3b4ed30a3d1bcdbd3d1448d5bc4bff553b7e28113c6744053d7fc44bbcea89253da26ac63b746b01bcedff2dbdae8432be4d557dbcb0dc1f3c42761fbcac2913bc1ca940bc06f4b23bbe931bbdbc7a063c7f57863de9cf9c3b923cac3db3980d3d639e4b3b5633073d691d81bddf0a1abc41aba13d9b13dabcdbac553d2daf133d26ba9bbc38f4183cf0f3973d	new	frontal	\N	\N	2026-10-14 14:54:19.514288+00	1	2026-10-14 14:54:19.514288+00
169	webcam	0	C:\\project\\yolov26-nhandien-biensoxe\\data\\snapshots\\face_webcam_20261012_231533_29d731991fc74dd19432e22c6267c71a.jpg	\N	\\xc0a77f3cf5cea4bd080da2bce8d85abd1916bc3d5cc0293d8c98483d349ac139f5fdc03c2f8992bdac130f3aca8380bca1e52d3c074c3b3de59ff73c3c3ebc3bb7ca24bdbd9c5b3da09a5c3dc799babc9bfca9bcfc138bbc2cf813bd05d52b3d5f72583df98c9d3c0b5be93c86f93e3df7fcdfbc5841803b16edba3d80e1f6bcd6bdce3b0c11043e6ecfa63c161d29bde7b3fdbc3a5d14be08e652bde3a838bd26763c3c68f797bcee76783d144947bd8a5fd5bd630c6fbdba2981bd0f9eb8bd94bd89bd68a49cbdd870593cbdd3463c23ccf33cc4ed64bd648fb43d5a0458bd873536bb973b24bd9b151b3b9f63acbde02b4bbc294c333d52db24bd939ee53d2cb0b13cd579333db1ef923c502d5f3b0518dd3cb5dd4abdd4c6aa3d3558393d08c9fd3bd29a25bda18b9dbd039545bcc960003d306f5f3dba86f7bc36c813bdba214b3d66b89ebcf887a5bc22c629bd0f94acbdb1830bbdeea106bd81934abc4c1c88bc6a5a6d3d6fe97e3c9913403d4fe5cabddd92c0bd8a11843d8ad7013dd088a23d02c58cbcda65aebc1ddaf7bcfe2495bbc194e43cffb84ebdc600683c5c48ef3c12ba1bbd51445f3d1cd08d3c76b193bcd20172bd4339e2bccb5c4dbc90bd7ebd8a1fa0bd594dbc3cbee5c23c9c79c93b67deb03c0c6ab43dff5645bdbec0e63c042882baca1e90bc2e8b8b3d8c716dbc3138bf3a5c5f953d500381bd0785e93a3aa360bda18c963dc755a73cbe375e3c346a04bd2dd39abb18f5a63bae2434bb2dcba23cc1d90a3d64f3a5bc2c35a13a416766b702b5af3b7fbb7d3c37949ebc9f8e81bd81998a3d5ecf1cbc90753cbdb4a1783c8ef1353b5efc953db713363d10945e3dccafc7bd395eacbbf3561fbcdfd791ba2a61003cc08e893de16aa1bcbb3249bc4def09bdc3364f3c1cd237bcc56f97bb94234e3dd80aa3bbe214b93d233566bd381513bd02944e3cf6a96cbd93b0ae3d23bb1abdd6dd853d26c6643ce8ce613c667a213d9b294d3dec6c86bd00d3763dc9c15a3dab4f7b3d0e863e3d9c4e3c3deebd1f3ac61c7cbb062b47bc8b6c263d7ffe26bc22f63c3bfc65343b3a067fbda6d49b3d11cc063dc12464bc4b4526bd7406e23cb4d3adbc3338a03c0b0e3cbd871df2bbe73688bc3ad3233b13a3eabb58b5b9bb249a9e3c26d1813c0ed366bd9ebca6bc5f99a9bcfcfada3cc43bbeba81572a3d5bc3f43b2af85ebc6ab322bc67ffe6bc5a32783cbdb3093dc1ba123d4c899f3b08e5903d0a58d2ba8aa69bbc1cf0123cde420b3dc9be2dbd1e0f08bc34e4b8bc00df10bde23f823c83216d3d26389c3c30f93c3dbaa2b2bc984c88bcf4f2953cc881843d97c0e63a52e1dbbc2fdc29bd02c4163cda51593d18150f3e84cf62bd01442d3c570195bd2682603decf3d83df6410e3c474a0dbd8682903b9e6cb83c39d8a63c886e6d3c34f2b33c8a197e3b3113b13d8738d4bde01e29bdccbf943c496748bb45c00e3dd4d11ebd8ac6833c3f2b81bca606903c74022fbcea39333a149482bd4cee2d3b3170aebd46699bbc493a45bd841f023cdb0268bd01d564bd41f6513b6670073d71e9ff3cd0b3463dc0fd90bdc07294bd08b21b3cfdf947bca59e75bdb63d793cde2a983cb18d3ab90a73143d2931443be9be4dbcd3a7593c2fae82bc5b67033cb3c73abd66878cbd8de942bd717ff1bca53ac1bc79b81dbdb767d3bc6ec9df3cc360fa3c3726863d309150ba900d10bdfee4bc3ced9a473d378661ba0f56623d09b030bdac6485bc0dbfc53df2f431bd81b4b6bc707ad9bbef67403c3abb3cbd3a2bd2bc77707abdbf88fdbc44b9e43d2593e03cc4eebfbc7ffeaebcd716babc94aab3bdec36c2bca721573d77f78fba4d03e43c8ab244bca603ffbc7d927b3c407871bcaf11e73cf42492bd36dc803ca1b2fdbb14702f3d0966d4bcc4b980bd0f3c3e3df3a112bb31dc8b3c7ee909bdc654553d11a5963c1c29313ddcc3e33c7c0df6bb8d5c243d23299bbb12c50cbc3d21933c1947103c077c65bdd4dcf5bcf90089bacfa53ebdd846a3bc060a483d6d64273dc7fc67bdb6663a3da9bf143c6d7cb03dfd70ebbd499d833c876dbabccc94e6bd663f1737e2db89bdaa3f09bd3f0c293da5db4b3c0815353dffdf6f3bd483d9bc3a0d63bc84026cbba16181bd91c8113dfcc347ba068793bc4f221bbc1689033daa87a2bcfec443bd08b54fbc6ef38e3dacd9babcd1ad363d7be4c3bd4fa114bd88fa01bc875d973d4d734e3d759038bd4e10c1bda5e7b5bcc661e33bc7acbcbc2737d2bcb6d94e3d5cff7cbd2b2559bc0be61e3c84bd5cbda1d293bdf24623bd10b678bd1d999fbd0cc1cb3cf458eb3df2ad1d384af911bdd7d58b3a15ae7e3c22570dbc530e98baaa6027bd7d395cbb3c9d8dbcf0e201bdd0cec3bdaaba95bcc09399bc113f5b3d3f2faf3b460e84bc1ce5813d5a9a033db2578d3c54290c3dce64583a33155c3c4bbc50bd51865dbc71be2ebc2ae44f3dd9a8e83c59aa65bdeac1033c7ca1e0bc422812bdcccf2cba72a784bda9b3eebc9270473d51622c3d3a31ebbc57f31a3dac4a6f3d4ede023b3fbd94bccbd880bc49571d3d319913bdf109d6bce3a71cbdc215e13d47de7c3d2b0c25bd25a11e3dc0ae24bd14ef9ebc355de1bd1bdcec3b417509bcecb1d3bc6dc3003d7b6d95bd660a96bd36606fbd116e4c3dff1dbbbbdb77073c75b18ebdb0ebf33a718799bda9452d3d865140bcd64fa13cebc24d3d5e1b143d5de307bd34f30bbd8d2a71bd03baacbd59f2d93c834801bcc99308bdd25e703d29fdf83b78a8b5bc4cf5ca3c0a2e05bd0d619d3cd489e23cdc3b8cbb393a893db12cf33bdc8f7bbcc6c0283dabbeabbd5e7ed73bf23ee0bbdfdfacbc	new	frontal	\N	\N	2026-10-12 16:15:33.159942+00	1	2026-10-12 16:15:33.159942+00
170	webcam	0	C:\\project\\yolov26-nhandien-biensoxe\\data\\snapshots\\face_webcam_20261012_231727_a6f78fbf603541a99f7a29fc6da1b2b5.jpg	\N	\\xf65700bc5a9027bd43e844bd594b0dbd9f7cec3ca496503c5f3bcc3ce990b83c529a94bc7cfc83bc82320dbd08cead3c076da83d4c811c3a1c07b73d37ead13ca65e7a3d639c8dbba2ed07bce92b3f3c8c0adf3c1a003a3dcba16a3b00fa013dfae84fbdc15728bdd995a3bc12cf7abca7f6d7bb6c53903b080f5b3d1ef421bc2efe89bbce1407bd5b3b64bc2d4faabaa9660f3cc385e6bb9ecfd8bc8f2a90bc0a88123d222b053d21708dbc8112e83c090e2f3daeb1c2bcb382913c1ebc00bd7b151cbd1389613d86bee93c2119d43cb76166bd99cd86bc0abb1e3dc5fa933d14fb6dbc473401bdfb2ec2bcf9dbcc3d5ef359bc3621923df37d90bdb8fb263da8ba063c75701a3cd3292f3d7bd789bdfb693c3c0c59663c572eab3df3ff723cd2dbd33c62ed65bc7db0b03c5b8daa3cfe738039703f8cbdecc75a3cd56282bd9ff99dbd6586bebc091a883bf17904bb225854bdf7af4e3d32e5c73ca04440bdf80a823c0e1f9e3d541844bc2d17e23ceb24a0bd4991f4bc3ffb703cc995433b1b3a17bdaafa8abd2985993df2ea81bd37ab3a3d34c2eebc347640bdd8ae173d63294abc22ebb23d0549953dfb5e9d3c0c1a693c0a5b833c99c855bcef82e9bc7c5dec3c90109a3a38da31bc785a22bcd633383d398082bd4669883d7b34b23d4bffeabcc035903d952c993d0220253d6add1ebd1d02c73d827c093cc2b7803d13cd21bde5ebb7bbb76b63bc7d6554bc689cd0bcaea8623d06258ebb672f95bc5526913dee0c32bd78126dbc4063e3bb8d19d8bb8fe1a23c755d35bdd998a6bc19de033c8364ad3a1a94e33dbb7fd039677a493bb7262e3d7f1c523d6f3c9d3cd2a95b3da32035bde9cf673d796723bccaf8dbbcc8dad4bd7b7b6d3d48599b3d4f3853bd4999433b648079bc16d3d53b1c9ae1bc5764a9bc7d22213db16e04bd65e3173d82a481bc4dd7ddbd4cbeacbd608d39bde4b2753c680ce63c5b7fa43bda131b3d812f1a3d8abfb13c2cf7623d07a7cb3cf80c913cfed8c4bcab17cd3c5a6017bc7bec9d3d26ce0dbdb82c95bdc2530f3d39ad55bcfc2cd63d0cc42dbd0878113c2ba0f1bd51751c3d6a27a3bc4f742abdd730abbd36aa43bd157a87bc68d1c53ce616143d4cca6cbddd7cf73b562841bdd57d863d6e0ca7bdf9bfcb3ca1fb12bd714fbdbc431e153d9eca0e3d26ce863b98b8823b739691bcdf06763dfd93803c1d9d96bbe0ce02bd50b42d3bfdb61c3c83569abde0929a3c3705583df025cd3c38ce83bcfaf9913da5c6163c43a089bd0ce7903df1cf4d3c597bfa3c8fff0b3ea6a1c9ba14560cbde88f0e3d652354bde7cc213bbe4a51bb44f6b1bdd95d623ded22e53ce05c6a3d839b2ebda405993d6d78b3bcca1c97bc1579493c9161343d3d211abdf2ecc33c5768293d4dd3bbbc2e388cbdec401f3da3dd303d8003023dffe5b73c23a50c3d5c3ba43deab37c3de9aaa13c9da48c3ce018483cda87703af79805bdb04c253da30d763c20ad4ebde89ae23838d74fbde3eb39bd9e0e3cbd7fe9b8bddc3d893df19991bb9c88f3bc0bbaee3b9007b6bc2e3a0cbd25b3f4ba165a8f3c9bdad63c0d8fa8bd97858bbd4ed2e43cdd2b19bac42d54bcf6f81cbdf690b7bca89d21bdd798573d8315513ccd1c34bdde2a273b190e063dddf17e3dbca305bb607918bd95969a3ddce81bbdd55bbfbc410b0bbe2ff640bddf870d3dc336263d5df084bb3de46fbb487ae7bb4e65f6b9df660fbcf59095bc436317bddc7f5abd17318dbdb167a93dcbb1f3bc4abd2abdf810c03c9bf95abc6d89c1bd9e351dbdaccdf33d60176f3de4c98bbd968c3dbcf4e6ed3c1fb883bcaee05c3db1d7b8bc24876ebd8799ad3c9c6550bcbfe87ebdc6792ebdd14c0ebda038babc36e928bd4588673d6498233dbab5f33b350a983be30f85ba41316c3c35992f3cf587e03c0d2c9f3cb9342bbc3edc9dbd8ba9f8bca1e4253d1a56a1bdd6f1363dccb970bbe70266bc7ea79d3d1693d53b52c620bcd706b4bc17b1343d073b813df50c83bd080ef7bc70caca3cfc65e9bb016bf9bbc1d850bc174baf3c1733103b8992863d1e34083c774ff8bbb30f6dbd3f4b3ebd5a4cc73b3ff787bdd3818fbcabbc3fbc091aa33ba3f0643d2835ce3cc6a13bbd3dbbd13cb257853cdd7152bc779daabc6608003d3226773d9d694c3d9eae3c3d299f36bdbc8f9fbcaa6a41bdea24f7bb7d8256bd1fd58a3da511153dc3e573bdc7e3d4bb4423febc637d6c3def4e87bc982e89bc0be1893d89832ebd6d42e8bdff9b913b30b7c83ca4955ebdee75be3d7f96ae3ba12b273c183e38bc927ee73cbfc4033dbb3708bdf8b0a0bc52a1393debb7ebbbb66bdd3d921a83bd74d7393dd0871b3cd9f9933be3eef03c46af3fbd178e9cbc2dbb25b9d79582bcc40833bc8f71733a824c8c3c944cec3b7ff3e8bcbe11ed3cdb1ec73c5ba59dbd572eed3b89edc53d6c1a933c08ed863d96809a3d6745babc38494e3c867f0bbd57472abda786ecbbad43473b29cac63aa25fc6bcb40ec13c1cc6263d1d32413d234b193d2fbd4b3ac5d0843dfee4363d90bfa83d742b713d53f637bd6e015dbd4195a73ddb069fbde126463d9d7189bc2ba2d33c3f28d4bc967fad3c018b733d0b1fdebd4d2f24bd7c4ab93da181f13c0418cebcd0e1473d3e34e63ae32d8c3dd8459abc8c1c273c8a2eaebcdb1d97bd1723df3d4d8e493cd4b093bcdd934fbc6967a2bcbb84803cbe82a4bd5ed6053c9048bd3ddd113c3d191a7a3c26350fbc5328073d6f4374bc1cdfbebc047d9f3cdb9bd2bdb81abebce7a1d5bcd82954bd5e6b5dbc859e9fbdcd8266bc838558bbebb515bce685e73c075ad73cc1165b3de117d5bc5135bebc7a71073d	new	frontal	\N	\N	2026-10-12 16:17:27.744165+00	1	2026-10-12 16:17:27.744165+00
174	webcam	0	C:\\project\\yolov26-nhandien-biensoxe\\data\\snapshots\\face_webcam_20261012_233958_0f1e2c7b10ce4e398504d4b9471edd2a.jpg	\N	\\xbb910ebd343b183c015816bba6a906bdf561433dfc8c87bdb9cb273db3be92bd9ec927bd2d4e1abbba6eb5bcb5729bbdd70e2dbdf055d53dcd8b35bd660e95bcc215893d590bfb3c61f5a53c38b26abd9f0e283d34cb81bd7a99ddbce2cb16bd7252dfbce97e583ce446033c6c1248bdcca2c2bb34262d3dce0f9c3d0c8bf53c2352903c91bc913c65628ebda18b0f3d3c46dd3ce7e65cbc7d445cbd425dcc3d7a63bcbd7756693d31f8083de010aa3b6df99e3ba42380bceae3713d00cd403d862ebd3d90598cbd93f8afbc4d2c06bd610daf3b3edd62bb9a66973cfdbcb7bcdd1b58bda06a043daaf848bd20d3a23c49fba43cdbef173c587c75ba210c2dbd5d9b79bb1c9fb3bd8f3f43bc559117bde33798bd99db04bd8306f93cd27e0d3dddaddfbd9cdbb4bcc35b08bc6403e0bcd9e00cbddb4d9d3bca7082bc63f888bd47dacbbc09de13bd0f5a05bcd3ac11bd3c973b39eb9b553dbb78e43c6437043dbf3791bd5031bebde39f17bd078bc0badc3254bc6b8b60bdaee499bbc38f2d3d47045b3d98b7933dd8dee63aadfd703dd85cc9bdc17a6bbd5610eabc3a97d5bc97657b3b443056bd06361e3d972be43cac2b9a3d0c980abd2da1a03cf0d3fabbbd1413bd782bb93ca37e33bcda81ec3c952c283cd476683c618a2dbbbea40a3d6795c53d5fdb1fbdafcd1dbd935fe43ceb85383c125376bdebccabbca009003d7f7f593d9ef539bdb6644e3b0f7a62bd78a7993d2284bc3cdccbd63c108880bdc92db43dbcfd06bda92aac3cccd0803d056a333d7087873de3a282bc1101e9bcbf5a48bd85a7e9b901454f3ca454e23c7e5eda3ced69aabc81215cbdd576663d250a7b3c58e8b3bcd3b1d2bdb66e4cbdc9bb813ceb5c0a3d789061bd9071a33d2028363c7a217f3c8d19a1bdfb1de33adb82d23b5987613c37cdeabb180a04bc03159bbbd6918cbd79e0cabc8e0f98bcd96eb7bb58423bbd55b435bdef84673ce559183d66bd0a3c1f6da5bde3db8aba5fe7183dee1eb5bc73723dbdf1c8aa3ba134a0bc0c25723c510f7b3ce455363b075f1a3d1407073ca8d2133d9ef91bbb725dabbc3bcd47bd66814e3c6b612ebc97084dbc8278933dcfece23d0534bfbd57e4ecb9d0233c3bc7b0d43ca491263d43dbf63cf46681ba3ddac43c399b7a3d577d473d2d6c063d295b37bd827d573c214daabc3fadabbcd8ae33bd27573abdd2aa1a3c08f670bdf3d927bcdb2a6cbd9892583cbcf2d53c1278deba4debafba068b85bdbd408b3b501a06bc4d16a8bbfbc1f73cf429a2bba45385ba4e880f3de1a110bc4d35a4bca787f13cca99953d50422d3d605f54bda07b783d7413ca3d6a9a94bdf92f82bd5e0e0b3da35a91bd772b633ce5439ebc609f37bd8bd108bd98f479bd4a703d3d710c753dd7fbf83c6d3b28bdee791abdc00f413b3760e83dcfc3413dd065a2bc191d723c3161a63d6ffbadbce2d3083c8199463c0424c53c4ab11c3d6656063daf31463d38d4433d33ffac3cdc33993d77bbbfbd7235fbbc886b73bc530a38bd828f993c861e20bc3138b8bc5d49c1bc673968bd3ead713d99b8c93c044d85bc856c0dbdbaf93ebd07e74cbc386d0c3b7f393b3de86863bde0c113bd0840823ccae45d3d570c2e3dc1de8cbd587c0f3d0fa33a3d1d828dba2a8b4f3d022e60bca2901cbb37da0f3da14a0cbdd68bc5bd6cc28f3c918c34bda03880bd4808a63c633aa8bd301c31bdd0df2e3dccac323a507a313dff6e2bbdc80c803ca2c8b5bd7627653de36e373d955d3fbc516f06bd6235423cdc85473c4dbd2cbd438831bdf731ecbc57ae203da873033d8b19bb3be26f42bd80df5dbd5e7fd1bbb952533cd46ebc3a6f38533cc57808bcca138c3c263e663c9f3dbc3d82c20cbd1239823dfa953f3d6406583ab5505c3d3a7bcabc53f6f03c4bf903bc829fa6bc3f20d73d5962e9bcaeda343d23c89a3d4fb61e3d2b6abe3c2e312d3c64c15dbdc6568fbdabe6cbbcfc095c3d333536bd811a94bc9fde0cbd9dac9b3d4279533db1f0b83c10d2393c512483bbf578083ec0ac483db1b7dd3cd16051bcc60c843b12efb03d54d3f7bb84115fbdbacbb43cfef69c3c0bcf123c9d5a373a059f20bd2c4564bc59794fbd9226be3b579563bd250c183d97a41fbe932d55bdd031aebc2099133c48c50c3b56d0a8bdee8612be779153bdc2f5ef3cfdd9bcbd3542713c639596390b6ac5bc370cf73a7f2a7e3b20712dbd3974593d21d7debc983acabb8b0a74bd4100d3bcb60e17bd4d4ded3a656b15bd764ccfbc93a8643d30e97d3c30049abc277c503c88de213d66f12d3dd7f5c03dd22947bdfc10f7bcd63badbb6f6f1c3d161a8e3ca12c2c3c172213bddb2a243db96b98bcfd212a3ce980a53d2f13bfbc13c3593c000e673c485c2bbc8f22103db507dd3c79fde0bc9eb6f93c6369623c325d0d3d1e63ac3c12e9583ddef1f9bb723988bc6b5b38bb2b5173bca4274f3c479cd83cb0e0c8bc9f6f1d3d3f3e383df10c58bd1c4997bc25c260bbc669c83bd45358bd62ca24bda3eba43da93147bd4efe7bbcb8ffb9bda2f039bdc7f42d3c42739ebd385e2dbb19a145bdfed3d8bbdd219bbc0d0352bd1bb3ab3bc77159bb20f345bdbd779c3b3394a339801f7cbcf53e75baaab71cbd766d183cf5d6af3d0f7e973d511d67bce42e6fbcfbfa09bce562dbbcef19933c1da696bd558bd7bb94ff68bc7ef3e8bcf8589f3c5e7cdcbd33b593bc5af93bbc093dc5bde625adbc8afe393c2e57043dd0dd4b3c705bb1bd89f3cf3b4d38b9bdabd9fe3b7a6bf73dc5000bbd931b983c24bb3b3c0780d43d879efebc1c5ca53c12240f3d2679253d97c388bc55263d3a3f42163c59b32bbdba9f4abd2fc99a3c	new	frontal	\N	\N	2026-10-12 16:39:58.258411+00	1	2026-10-12 16:39:58.258411+00
\.


--
-- Data for Name: telegram_bots; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.telegram_bots (id, bot_name, token, chat_id, is_active, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: users; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.users (id, username, hashed_password, role_id, full_name, note, site_ids, person_identity, phone, email, date_of_birth, id_number, kyc_level, kyc_submitted_at, kyc_reviewed_at, kyc_reviewed_by, kyc_reject_reason, remember_token, remember_token_expires, created_at, updated_at) FROM stdin;
1	admin	$2b$12$9MoZnNIQRcAUc1DNqAMSWOWBgiHer/dz9n0zMJybOUryiqYuOXHwO	1	Administrator	Seeded default admin account	{}	\N	\N	\N	\N	\N	4	\N	\N	\N	\N	fJDVxFu3BIlliHzoCFA2HH0XqYIxdqAdGtOgHDs-aM4	2026-06-06 01:20:27.779634+00	2026-05-16 07:34:13.832404+00	2026-05-16 07:55:05.125573+00
\.


--
-- Data for Name: vehicle_sessions; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.vehicle_sessions (id, plate_norm, vehicle_type, camera_id_in, camera_id_out, entry_time, exit_time, entry_image, exit_image, site_id, status, stay_duration, fee, created_at, updated_at) FROM stdin;
\.


--
-- Data for Name: video_recordings; Type: TABLE DATA; Schema: public; Owner: camera_user
--

COPY public.video_recordings (id, cam_id, site_id, event_type, event_text, file_path, thumbnail_path, start_time, duration_seconds, file_size_bytes, is_favorite, created_at) FROM stdin;
\.


--
-- Name: admin_seeds_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.admin_seeds_id_seq', 36, true);


--
-- Name: camera_providers_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.camera_providers_id_seq', 24, true);


--
-- Name: camera_status_log_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.camera_status_log_id_seq', 29, true);


--
-- Name: cameras_asset_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.cameras_asset_id_seq', 248, true);


--
-- Name: cameras_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.cameras_id_seq', 9, true);


--
-- Name: cargo_trips_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.cargo_trips_id_seq', 1, true);


--
-- Name: door_providers_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.door_providers_id_seq', 17, true);


--
-- Name: inquiries_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.inquiries_id_seq', 1, true);


--
-- Name: kyc_documents_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.kyc_documents_id_seq', 19, true);


--
-- Name: legal_hold_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.legal_hold_id_seq', 1, false);


--
-- Name: monthly_reports_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.monthly_reports_id_seq', 1, false);


--
-- Name: news_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.news_id_seq', 1, false);


--
-- Name: ocr_training_samples_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.ocr_training_samples_id_seq', 1, false);


--
-- Name: pending_plates_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.pending_plates_id_seq', 52, true);


--
-- Name: plate_events_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.plate_events_id_seq', 1, false);


--
-- Name: plate_whitelist_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.plate_whitelist_id_seq', 5, true);


--
-- Name: projects_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.projects_id_seq', 1, false);


--
-- Name: roles_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.roles_id_seq', 3, true);


--
-- Name: rolling_doors_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.rolling_doors_id_seq', 1, true);


--
-- Name: sites_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.sites_id_seq', 3, true);


--
-- Name: stranger_logs_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.stranger_logs_id_seq', 176, true);


--
-- Name: telegram_bots_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.telegram_bots_id_seq', 1, false);


--
-- Name: users_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.users_id_seq', 5, true);


--
-- Name: vehicle_sessions_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.vehicle_sessions_id_seq', 1, false);


--
-- Name: video_recordings_id_seq; Type: SEQUENCE SET; Schema: public; Owner: camera_user
--

SELECT pg_catalog.setval('public.video_recordings_id_seq', 80, true);


--
-- Name: admin_seeds admin_seeds_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.admin_seeds
    ADD CONSTRAINT admin_seeds_pkey PRIMARY KEY (id);


--
-- Name: admin_seeds admin_seeds_username_key; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.admin_seeds
    ADD CONSTRAINT admin_seeds_username_key UNIQUE (username);


--
-- Name: app_settings app_settings_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.app_settings
    ADD CONSTRAINT app_settings_pkey PRIMARY KEY (key);


--
-- Name: camera_providers camera_providers_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.camera_providers
    ADD CONSTRAINT camera_providers_pkey PRIMARY KEY (provider_key);


--
-- Name: camera_providers camera_providers_provider_key_key; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.camera_providers
    ADD CONSTRAINT camera_providers_provider_key_key UNIQUE (provider_key);


--
-- Name: camera_status_log camera_status_log_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.camera_status_log
    ADD CONSTRAINT camera_status_log_pkey PRIMARY KEY (id);


--
-- Name: cameras_asset cameras_asset_cam_id_key; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.cameras_asset
    ADD CONSTRAINT cameras_asset_cam_id_key UNIQUE (cam_id);


--
-- Name: cameras_asset cameras_asset_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.cameras_asset
    ADD CONSTRAINT cameras_asset_pkey PRIMARY KEY (id);


--
-- Name: cameras cameras_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.cameras
    ADD CONSTRAINT cameras_pkey PRIMARY KEY (id);


--
-- Name: cargo_trips cargo_trips_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.cargo_trips
    ADD CONSTRAINT cargo_trips_pkey PRIMARY KEY (id);


--
-- Name: door_providers door_providers_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.door_providers
    ADD CONSTRAINT door_providers_pkey PRIMARY KEY (provider_key);


--
-- Name: door_providers door_providers_provider_key_key; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.door_providers
    ADD CONSTRAINT door_providers_provider_key_key UNIQUE (provider_key);


--
-- Name: inquiries inquiries_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.inquiries
    ADD CONSTRAINT inquiries_pkey PRIMARY KEY (id);


--
-- Name: kyc_documents kyc_documents_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.kyc_documents
    ADD CONSTRAINT kyc_documents_pkey PRIMARY KEY (id);


--
-- Name: kyc_documents kyc_documents_user_id_doc_type_key; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.kyc_documents
    ADD CONSTRAINT kyc_documents_user_id_doc_type_key UNIQUE (user_id, doc_type);


--
-- Name: legal_hold legal_hold_file_path_key; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.legal_hold
    ADD CONSTRAINT legal_hold_file_path_key UNIQUE (file_path);


--
-- Name: legal_hold legal_hold_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.legal_hold
    ADD CONSTRAINT legal_hold_pkey PRIMARY KEY (id);


--
-- Name: monthly_reports monthly_reports_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.monthly_reports
    ADD CONSTRAINT monthly_reports_pkey PRIMARY KEY (id);


--
-- Name: monthly_reports monthly_reports_report_month_key; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.monthly_reports
    ADD CONSTRAINT monthly_reports_report_month_key UNIQUE (report_month);


--
-- Name: news news_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.news
    ADD CONSTRAINT news_pkey PRIMARY KEY (id);


--
-- Name: news news_slug_key; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.news
    ADD CONSTRAINT news_slug_key UNIQUE (slug);


--
-- Name: ocr_training_samples ocr_training_samples_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.ocr_training_samples
    ADD CONSTRAINT ocr_training_samples_pkey PRIMARY KEY (id);


--
-- Name: pending_plates pending_plates_pending_id_key; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.pending_plates
    ADD CONSTRAINT pending_plates_pending_id_key UNIQUE (pending_id);


--
-- Name: pending_plates pending_plates_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.pending_plates
    ADD CONSTRAINT pending_plates_pkey PRIMARY KEY (id);


--
-- Name: plate_events plate_events_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.plate_events
    ADD CONSTRAINT plate_events_pkey PRIMARY KEY (id);


--
-- Name: plate_whitelist plate_whitelist_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.plate_whitelist
    ADD CONSTRAINT plate_whitelist_pkey PRIMARY KEY (id);


--
-- Name: plate_whitelist plate_whitelist_plate_number_key; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.plate_whitelist
    ADD CONSTRAINT plate_whitelist_plate_number_key UNIQUE (plate_number);


--
-- Name: projects projects_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.projects
    ADD CONSTRAINT projects_pkey PRIMARY KEY (id);


--
-- Name: roles roles_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT roles_pkey PRIMARY KEY (id);


--
-- Name: roles roles_role_name_key; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.roles
    ADD CONSTRAINT roles_role_name_key UNIQUE (role_name);


--
-- Name: rolling_doors rolling_doors_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.rolling_doors
    ADD CONSTRAINT rolling_doors_pkey PRIMARY KEY (id);


--
-- Name: sites sites_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.sites
    ADD CONSTRAINT sites_pkey PRIMARY KEY (id);


--
-- Name: sites sites_site_name_key; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.sites
    ADD CONSTRAINT sites_site_name_key UNIQUE (site_name);


--
-- Name: stranger_logs stranger_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.stranger_logs
    ADD CONSTRAINT stranger_logs_pkey PRIMARY KEY (id);


--
-- Name: telegram_bots telegram_bots_bot_name_key; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.telegram_bots
    ADD CONSTRAINT telegram_bots_bot_name_key UNIQUE (bot_name);


--
-- Name: telegram_bots telegram_bots_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.telegram_bots
    ADD CONSTRAINT telegram_bots_pkey PRIMARY KEY (id);


--
-- Name: users users_person_identity_key; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_person_identity_key UNIQUE (person_identity);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: users users_remember_token_key; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_remember_token_key UNIQUE (remember_token);


--
-- Name: users users_username_key; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_username_key UNIQUE (username);


--
-- Name: vehicle_sessions vehicle_sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.vehicle_sessions
    ADD CONSTRAINT vehicle_sessions_pkey PRIMARY KEY (id);


--
-- Name: video_recordings video_recordings_file_path_key; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.video_recordings
    ADD CONSTRAINT video_recordings_file_path_key UNIQUE (file_path);


--
-- Name: video_recordings video_recordings_pkey; Type: CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.video_recordings
    ADD CONSTRAINT video_recordings_pkey PRIMARY KEY (id);


--
-- Name: idx_asset_ip; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_asset_ip ON public.cameras_asset USING btree (ip);


--
-- Name: idx_camera_status_log_camera_time; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_camera_status_log_camera_time ON public.camera_status_log USING btree (camera_id, logged_at DESC);


--
-- Name: idx_cameras_active; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_cameras_active ON public.cameras USING btree (is_active);


--
-- Name: idx_cameras_site_ids; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_cameras_site_ids ON public.cameras USING gin (site_ids);


--
-- Name: idx_cargo_trips_vehicle_departure; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_cargo_trips_vehicle_departure ON public.cargo_trips USING btree (vehicle_id, departure_time DESC);


--
-- Name: idx_inquiries_read_time; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_inquiries_read_time ON public.inquiries USING btree (is_read, created_at DESC);


--
-- Name: idx_kyc_docs_status; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_kyc_docs_status ON public.kyc_documents USING btree (status);


--
-- Name: idx_kyc_docs_user; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_kyc_docs_user ON public.kyc_documents USING btree (user_id);


--
-- Name: idx_news_publish_date; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_news_publish_date ON public.news USING btree (publish_date DESC);


--
-- Name: idx_ocr_training_samples_status_time; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_ocr_training_samples_status_time ON public.ocr_training_samples USING btree (status, created_at DESC);


--
-- Name: idx_pending_plates_plate_norm; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_pending_plates_plate_norm ON public.pending_plates USING btree (plate_norm);


--
-- Name: idx_pending_plates_status_time; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_pending_plates_status_time ON public.pending_plates USING btree (status, first_seen_utc DESC);


--
-- Name: idx_plate_events_plate; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_plate_events_plate ON public.plate_events USING btree (plate_number);


--
-- Name: idx_plate_events_time; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_plate_events_time ON public.plate_events USING btree (event_time DESC);


--
-- Name: idx_plate_events_time_conf; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_plate_events_time_conf ON public.plate_events USING btree (event_time, confidence);


--
-- Name: idx_plate_whitelist_active; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_plate_whitelist_active ON public.plate_whitelist USING btree (is_active);


--
-- Name: idx_plate_whitelist_site_ids; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_plate_whitelist_site_ids ON public.plate_whitelist USING gin (site_ids);


--
-- Name: idx_rolling_doors_site; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_rolling_doors_site ON public.rolling_doors USING btree (site_id);


--
-- Name: idx_rolling_doors_supplier; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_rolling_doors_supplier ON public.rolling_doors USING btree (supplier_key);


--
-- Name: idx_stranger_logs_site; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_stranger_logs_site ON public.stranger_logs USING btree (site_id);


--
-- Name: idx_stranger_logs_status; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_stranger_logs_status ON public.stranger_logs USING btree (status);


--
-- Name: idx_stranger_logs_status_time; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_stranger_logs_status_time ON public.stranger_logs USING btree (status, event_time DESC);


--
-- Name: idx_stranger_logs_time; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_stranger_logs_time ON public.stranger_logs USING btree (event_time DESC);


--
-- Name: idx_users_remember_token; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_users_remember_token ON public.users USING btree (remember_token);


--
-- Name: idx_users_role_id; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_users_role_id ON public.users USING btree (role_id);


--
-- Name: idx_users_site_ids; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_users_site_ids ON public.users USING gin (site_ids);


--
-- Name: idx_vehicle_sessions_entered; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_vehicle_sessions_entered ON public.vehicle_sessions USING btree (entry_time);


--
-- Name: idx_vehicle_sessions_entry_time; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_vehicle_sessions_entry_time ON public.vehicle_sessions USING btree (entry_time DESC);


--
-- Name: idx_vehicle_sessions_plate_status; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_vehicle_sessions_plate_status ON public.vehicle_sessions USING btree (plate_norm, status);


--
-- Name: idx_video_recordings_cam_time; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_video_recordings_cam_time ON public.video_recordings USING btree (cam_id, start_time DESC);


--
-- Name: idx_video_recordings_event; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_video_recordings_event ON public.video_recordings USING btree (event_type);


--
-- Name: idx_video_recordings_site_time; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_video_recordings_site_time ON public.video_recordings USING btree (site_id, start_time DESC);


--
-- Name: idx_whitelist_site_ids; Type: INDEX; Schema: public; Owner: camera_user
--

CREATE INDEX idx_whitelist_site_ids ON public.plate_whitelist USING gin (site_ids);


--
-- Name: cargo_trips cargo_trips_vehicle_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.cargo_trips
    ADD CONSTRAINT cargo_trips_vehicle_id_fkey FOREIGN KEY (vehicle_id) REFERENCES public.plate_whitelist(id) ON DELETE CASCADE;


--
-- Name: kyc_documents kyc_documents_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.kyc_documents
    ADD CONSTRAINT kyc_documents_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: pending_plates pending_plates_event_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.pending_plates
    ADD CONSTRAINT pending_plates_event_id_fkey FOREIGN KEY (event_id) REFERENCES public.plate_events(id) ON DELETE SET NULL;


--
-- Name: pending_plates pending_plates_site_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.pending_plates
    ADD CONSTRAINT pending_plates_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.sites(id) ON DELETE SET NULL;


--
-- Name: plate_events plate_events_site_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.plate_events
    ADD CONSTRAINT plate_events_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.sites(id) ON DELETE SET NULL;


--
-- Name: rolling_doors rolling_doors_camera_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.rolling_doors
    ADD CONSTRAINT rolling_doors_camera_id_fkey FOREIGN KEY (camera_id) REFERENCES public.cameras(id) ON DELETE SET NULL;


--
-- Name: rolling_doors rolling_doors_site_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.rolling_doors
    ADD CONSTRAINT rolling_doors_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.sites(id) ON DELETE SET NULL;


--
-- Name: rolling_doors rolling_doors_supplier_key_fkey; Type: FK CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.rolling_doors
    ADD CONSTRAINT rolling_doors_supplier_key_fkey FOREIGN KEY (supplier_key) REFERENCES public.door_providers(provider_key) ON DELETE SET NULL;


--
-- Name: stranger_logs stranger_logs_converted_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.stranger_logs
    ADD CONSTRAINT stranger_logs_converted_user_id_fkey FOREIGN KEY (converted_user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: stranger_logs stranger_logs_site_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.stranger_logs
    ADD CONSTRAINT stranger_logs_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.sites(id) ON DELETE SET NULL;


--
-- Name: users users_role_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_role_id_fkey FOREIGN KEY (role_id) REFERENCES public.roles(id) ON DELETE RESTRICT;


--
-- Name: vehicle_sessions vehicle_sessions_site_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.vehicle_sessions
    ADD CONSTRAINT vehicle_sessions_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.sites(id) ON DELETE SET NULL;


--
-- Name: video_recordings video_recordings_site_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: camera_user
--

ALTER TABLE ONLY public.video_recordings
    ADD CONSTRAINT video_recordings_site_id_fkey FOREIGN KEY (site_id) REFERENCES public.sites(id) ON DELETE SET NULL;


--
-- PostgreSQL database dump complete
--

\unrestrict EwOIx8KQy02uiyTFhSjuzRNjYQ5upVe7QQEz2MiaB3iPmHZ8DoRA2n1WV8ivXrZ

