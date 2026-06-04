import streamlit as st
import requests
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime
import time

# Helper function to prevent deprecation warnings in modern Streamlit versions
def safe_rerun():
    if hasattr(st, "rerun"):
        st.rerun()
    else:
        st.experimental_rerun()

# Page Configuration for Wide Layout & Title
st.set_page_config(
    page_title="Purplle Store Intelligence Dashboard",
    page_icon="🛍️",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Backend URL Configuration
BACKEND_URL = st.sidebar.text_input("Backend API URL", "http://127.0.0.1:8000")

# Custom Premium Styling CSS
st.markdown("""
<style>
    /* Metric styling - Lighter purple for better contrast */
    div[data-testid="stMetric"] {
        background-color: #4A1E58;
        border: 1px solid #9C42B3; 
        padding: 20px;
        border-radius: 12px;
        box-shadow: 0 4px 12px rgba(0, 0, 0, 0.5);
    }
    div[data-testid="stMetricLabel"] p {
        color: #F0C4FA !important; /* Lighter soft purple text */
        font-size: 15px !important;
        text-transform: uppercase;
        font-weight: 700;
        letter-spacing: 0.8px;
    }
    div[data-testid="stMetricValue"] div {
        color: #FFFFFF !important;
        font-size: 34px !important;
        font-weight: 900;
        text-shadow: 1px 1px 2px rgba(0,0,0,0.3);
    }
    
    /* Custom Alerts style */
    .alert-card {
        padding: 15px 18px;
        border-radius: 8px;
        margin-bottom: 12px;
        border-left: 6px solid;
        color: #F8EDFA;
        background-color: #381A46; 
        box-shadow: 0 2px 5px rgba(0,0,0,0.2);
    }
    .alert-critical {
        background-color: #4A1324;
        border-left-color: #FF2E63; 
    }
    .alert-warning {
        background-color: #4A3412;
        border-left-color: #FFB300;
    }
    .alert-info {
        background-color: #381A46;
        border-left-color: #BD42ED;
    }
    
    /* Headers and Links */
    h1, h2, h3 {
        color: #FFFFFF !important;
        font-weight: 800 !important;
    }
    h4, h5, h6 {
        color: #E2B2EC !important;
        font-weight: 700 !important;
    }
    
    /* Sidebar adjustments */
    [data-testid="stSidebar"] {
        border-right: 1px solid #4A1E58;
    }
</style>
""", unsafe_allow_html=True)

# Helper Function to query FastAPI Backend
def fetch_api(endpoint: str, method="GET", json_data=None):
    try:
        url = f"{BACKEND_URL}/{endpoint.lstrip('/')}"
        if method == "GET":
            response = requests.get(url, timeout=3)
        elif method == "POST":
            response = requests.post(url, json=json_data, timeout=3)
        else:
            response = requests.get(url, timeout=3)
        
        if response.status_code in [200, 201]:
            return response.json()
        return None
    except Exception:
        return None

# --- DASHBOARD HEADER ---
col_logo, col_title = st.columns([1, 12])
with col_title:
    st.markdown("# 🛍️ Purplle Store Intelligence System")
    st.markdown("##### *AI-Powered CCTV Store Analytics Dashboard for Operations & Queue Optimization*")
st.markdown("---")

# Test Connection to API Backend
backend_status = fetch_api("/")
if not backend_status:
    st.error("🔌 Unable to connect to the FastAPI Backend Server! Please verify that your backend server is running at " + BACKEND_URL)
    st.info("💡 Run the following command in your terminal to boot the backend: `uvicorn app.main:app --port 8000 --reload`")
    st.stop()

# --- SIDEBAR CONTROL PANEL ---
st.sidebar.markdown("## ⚙️ Control Center")

# Fetch Cameras List
cameras = fetch_api("/api/cameras")
if not cameras:
    st.sidebar.warning("No cameras configured.")
    st.stop()

camera_dict = {cam["name"]: cam["id"] for cam in cameras}
selected_cam_name = st.sidebar.selectbox("Select CCTV Feed", list(camera_dict.keys()))
selected_cam_id = camera_dict[selected_cam_name]
selected_cam = next(c for c in cameras if c["id"] == selected_cam_id)

# Fetch Current Running Stream Telemetry to check if thread is active
realtime_metrics = fetch_api(f"/api/analytics/realtime/{selected_cam_id}")

# Start / Stop Stream Thread Controls
st.sidebar.markdown("### 📽️ Stream Status")
is_active_thread = selected_cam_id in backend_status.get("active_camera_threads", [])

if is_active_thread:
    st.sidebar.success("🟢 Active & Processing Live Footage")
    if st.sidebar.button("⏹️ Stop Stream Processing"):
        fetch_api(f"/api/cameras/{selected_cam_id}/stop", method="POST")
        safe_rerun()
else:
    st.sidebar.warning("🔴 Idle (Video Processing Stopped)")
    if st.sidebar.button("▶️ Start Stream Processing"):
        fetch_api(f"/api/cameras/{selected_cam_id}/start", method="POST")
        safe_rerun()

# Select View Mode (Original Feed vs Heatmap)
view_mode = st.sidebar.radio(
    "Select Video View Mode",
    ["Standard Feed (Annotated BBoxes)", "Live Activity Heatmap"],
    index=0
)

# Camera Channels Manager panel
st.sidebar.markdown("### 🛒 Camera Channels Manager")
with st.sidebar.expander("🛠️ Manage Camera Sources", expanded=False):
    st.markdown("##### ➕ Register New Camera")
    new_cam_name = st.text_input("Name", placeholder="e.g. Back Store Room", key="new_cam_name")
    new_cam_url = st.text_input("Stream Source", placeholder="demo or rtsp://... or local.mp4", key="new_cam_url")
    if st.button("Add Camera Channel", use_container_width=True):
        if new_cam_name and new_cam_url:
            res = fetch_api("/api/cameras", method="POST", json_data={"name": new_cam_name, "stream_url": new_cam_url})
            if res:
                st.success(f"Added: {new_cam_name}")
                time.sleep(1.0)
                safe_rerun()
            else:
                st.error("Failed to register camera.")
        else:
            st.warning("Please specify both Name and Source.")

    if len(cameras) > 1:
        st.markdown("---")
        st.markdown("##### 🗑️ Remove Camera")
        cams_to_delete = [cam["name"] for cam in cameras if cam["name"] != selected_cam_name]
        cam_to_delete = st.selectbox("Select camera to delete", cams_to_delete, key="cam_to_delete")
        if st.button("Delete Camera Channel", use_container_width=True):
            del_id = camera_dict[cam_to_delete]
            try:
                response = requests.delete(f"{BACKEND_URL}/api/cameras/{del_id}", timeout=3)
                if response.status_code == 204:
                    st.success(f"Deleted: {cam_to_delete}")
                    time.sleep(1.0)
                    safe_rerun()
                else:
                    st.error("Failed to delete camera channel.")
            except Exception as e:
                st.error(f"Error: {e}")

# Settings panel for Auto-Refresh controls
st.sidebar.markdown("### ⚙️ Dashboard Settings")
auto_refresh = st.sidebar.checkbox("Auto-Refresh Dashboard Feed", value=True)
refresh_interval = st.sidebar.slider("Refresh Interval (seconds)", min_value=1, max_value=10, value=3)

# Threshold visual indicators (informative only, set in config)
st.sidebar.markdown("### 🎛️ Configuration Thresholds")
st.sidebar.caption("Configured globally in app/config.py")
st.sidebar.info(f"""
- **Loitering Time**: `15s`
- **Crowd Capacity**: `4 persons`
- **Queue Max Congestion**: `3 persons`
""")

# --- MAIN DASHBOARD BODY ---

# Row 1: KPI Telemetry Cards
if realtime_metrics:
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric(
            label="Total Store Occupancy", 
            value=f"{realtime_metrics['total_occupancy']} persons"
        )
    with col2:
        q_alert = "⚠️ Congestion Alert" if realtime_metrics['queue_alert'] else "Normal Wait"
        st.metric(
            label="Checkout Queue Wait", 
            value=f"{realtime_metrics['queue_occupancy']} persons", 
            delta=q_alert,
            delta_color="inverse" if realtime_metrics['queue_alert'] else "normal"
        )
    with col3:
        st.metric(
            label="Main Floor Browsing", 
            value=f"{realtime_metrics['store_occupancy']} persons"
        )
    with col4:
        st.metric(
            label="Active Loitering Alerts", 
            value=realtime_metrics['active_loiterers_count'],
            delta="Suspicious" if realtime_metrics['active_loiterers_count'] > 0 else "Safe",
            delta_color="inverse" if realtime_metrics['active_loiterers_count'] > 0 else "normal"
        )

# Row 2: Live Video Stream & Active Alerts side-by-side
st.markdown("### 🎥 Live Visual Stream and Event Stream")
col_feed, col_alerts = st.columns([8, 4])

with col_feed:
    if is_active_thread:
        # Determine appropriate MJPEG source URL
        stream_path = "live" if view_mode == "Standard Feed (Annotated BBoxes)" else "live-heatmap"
        feed_url = f"{BACKEND_URL}/api/cameras/{selected_cam_id}/{stream_path}"
        
        # Display the live stream with responsive width
        st.image(feed_url, use_container_width=True)
    else:
        st.markdown(
            """
            <div style="background-color: #1A1A24; border: 2px dashed #3E3E56; border-radius: 12px; height: 420px; display: flex; flex-direction: column; justify-content: center; align-items: center; text-align: center; color: #8F8F9F; padding: 20px;">
                <h3 style="margin-bottom: 10px;">🎥 Camera Feed is Offline</h3>
                <p style="margin-bottom: 20px;">The AI visual analyzer thread is currently stopped for this camera feed.</p>
                <p style="font-size: 14px; color: #6E6E7F;">Click the <b>▶️ Start Stream Processing</b> button in the sidebar to activate real-time detection & tracking.</p>
            </div>
            """, 
            unsafe_allow_html=True
        )

with col_alerts:
    st.markdown("#### 🚨 Real-time Security & Queue Alerts")
    
    if realtime_metrics and realtime_metrics.get("recent_events"):
        for event in realtime_metrics["recent_events"]:
            sev = event["severity"].upper()
            etype = event["event_type"].upper().replace("_", " ")
            msg = event["message"]
            ts = datetime.fromisoformat(event["timestamp"].replace("Z", "")).strftime("%H:%M:%S")
            
            # Severity color coding
            card_class = "alert-info"
            if sev == "CRITICAL":
                card_class = "alert-critical"
            elif sev == "WARNING":
                card_class = "alert-warning"
                
            st.markdown(
                f"""
                <div class="alert-card {card_class}">
                    <span style="font-weight: 800; font-size: 12px;">[{ts}] {sev} - {etype}</span><br/>
                    <span style="font-size: 14px;">{msg}</span>
                </div>
                """,
                unsafe_allow_html=True
            )
    else:
        st.markdown(
            """
            <div style="background-color: #1A1A24; border: 1px solid #2C2C3E; border-radius: 8px; padding: 20px; text-align: center; color: #8F8F9F;">
                🟢 No active events or alerts triggered on this stream.
            </div>
            """,
            unsafe_allow_html=True
        )

# Row 3: Historical Plotting & Analytics
st.markdown("### 📊 Store Traffic & Queue Congestion Analytics")

historical_snapshots = fetch_api(f"/api/analytics/historical/{selected_cam_id}?hours=6")

if historical_snapshots and len(historical_snapshots) > 1:
    # Convert snapshots to Pandas DataFrame
    df = pd.DataFrame(historical_snapshots)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    # Convert timestamp to local timezone for better plotting
    df["time_str"] = df["timestamp"].dt.strftime("%H:%M:%S")

    col_chart1, col_chart2 = st.columns(2)
    
    with col_chart1:
        st.markdown("#### Customer Traffic Trends")
        
        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=df["time_str"], y=df["total_occupancy"],
            mode='lines', name='Total Store',
            line=dict(color='#8A2BE2', width=3)  # Purplle Violet
        ))
        fig.add_trace(go.Scatter(
            x=df["time_str"], y=df["store_occupancy"],
            mode='lines', name='Shopping Aisles',
            line=dict(color='#00FFCC', width=2, dash='dot')
        ))
        fig.add_trace(go.Scatter(
            x=df["time_str"], y=df["queue_occupancy"],
            mode='lines', name='Checkout Queue',
            line=dict(color='#FFA500', width=2)
        ))
        
        fig.update_layout(
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font_color='#E2E2E9',
            xaxis=dict(showgrid=True, gridcolor='#22222E', title="Time"),
            yaxis=dict(showgrid=True, gridcolor='#22222E', title="Head Count"),
            margin=dict(l=20, r=20, t=10, b=20),
            legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
        )
        st.plotly_chart(fig, use_container_width=True)
        
    with col_chart2:
        st.markdown("#### Live Store Wait times vs Area Distribution")
        
        # Calculate recent dwell time metric
        fig = px.bar(
            df.tail(20), x="time_str", y="avg_dwell_time",
            labels={"time_str": "Time", "avg_dwell_time": "Average Dwell Time (s)"},
            color_discrete_sequence=['#FF4B4B']
        )
        fig.update_layout(
            paper_bgcolor='rgba(0,0,0,0)',
            plot_bgcolor='rgba(0,0,0,0)',
            font_color='#E2E2E9',
            xaxis=dict(showgrid=False, title="Time"),
            yaxis=dict(showgrid=True, gridcolor='#22222E', title="Dwell Time (Seconds)"),
            margin=dict(l=20, r=20, t=10, b=20)
        )
        st.plotly_chart(fig, use_container_width=True)
else:
    st.info("📊 Collecting sufficient data to plot historical trends... Visual charts will display automatically after a few database snapshots are captured.")

# Row 4: Historical Database Logs Grid
st.markdown("### 🗄️ System Events & Detection Logs")

# Filters row
col_filt1, col_filt2, col_filt3 = st.columns(3)
with col_filt1:
    filt_type = st.selectbox(
        "Filter by Event Category",
        ["ALL", "LOITERING", "CROWD_ALERT", "QUEUE_ALERT"]
    )
with col_filt2:
    filt_sev = st.selectbox(
        "Filter by Severity",
        ["ALL", "INFO", "WARNING", "CRITICAL"]
    )
with col_filt3:
    st.write("") # spacer
    st.write("") # spacer
    if st.button("🔄 Refresh System Logs"):
        safe_rerun()

# Build query string
query_path = f"/api/events?camera_id={selected_cam_id}&limit=100"
if filt_type != "ALL":
    query_path += f"&event_type={filt_type}"
if filt_sev != "ALL":
    query_path += f"&severity={filt_sev}"

events_log = fetch_api(query_path)

if events_log:
    # Convert logs list into structured Pandas dataframe
    log_df = pd.DataFrame(events_log)
    
    # Beautify timestamp
    log_df["timestamp"] = pd.to_datetime(log_df["timestamp"]).dt.strftime("%Y-%m-%d %H:%M:%S")
    
    # Reorder columns
    display_df = log_df[["id", "timestamp", "event_type", "severity", "message"]]
    display_df.columns = ["ID", "Timestamp", "Alert Event Category", "Severity Level", "Alert Details"]
    
    # Render interactive search-enabled data table
    st.dataframe(
        display_df, 
        use_container_width=True, 
        hide_index=True
    )
else:
    st.markdown(
        """
        <div style="background-color: #1A1A24; border: 1px solid #2C2C3E; border-radius: 8px; padding: 30px; text-align: center; color: #8F8F9F;">
            📭 No matching event logs found in the database.
        </div>
        """,
        unsafe_allow_html=True
    )

# Automated Refresh trigger (re-runs dashboard page based on user settings to keep counts live)
if auto_refresh:
    time.sleep(refresh_interval)
    safe_rerun()
