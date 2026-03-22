import { useState, useEffect, useCallback } from "react";

// ─── CONFIG ───────────────────────────────────────────────
const API_BASE = "http://192.168.154.107:3000";
const ROOMS    = ["HALL-A", "HALL-B", "HALL-C"];
const POLL_MS  = 3000;

// ─── BADGE ────────────────────────────────────────────────
const Badge = ({ text, color }) => {
  const colors = {
    green:  "bg-green-100 text-green-800 border-green-300",
    red:    "bg-red-100 text-red-800 border-red-300",
    yellow: "bg-yellow-100 text-yellow-800 border-yellow-300",
    blue:   "bg-blue-100 text-blue-800 border-blue-300",
    gray:   "bg-gray-100 text-gray-600 border-gray-300",
  };
  return (
    <span className={`px-2 py-0.5 rounded-full text-xs font-semibold border ${colors[color] || colors.gray}`}>
      {text}
    </span>
  );
};

// ─── METHOD BADGE ─────────────────────────────────────────
const MethodBadge = ({ method }) => {
  const map = {
    "rfid+face":   { label: "RFID + Face",   color: "green"  },
    "rfid+qr":     { label: "RFID + QR",     color: "blue"   },
    "face+qr":     { label: "Face + QR",     color: "blue"   },
    "student_pin": { label: "Emergency PIN", color: "yellow" },
    "faculty_pin": { label: "Faculty PIN",   color: "blue"   },
    "none":        { label: "UNAUTHORIZED",  color: "red"    },
  };
  const { label, color } = map[method] ||
    { label: method || "—", color: "gray" };
  return <Badge text={label} color={color} />;
};

// ─── STAT CARD ────────────────────────────────────────────
const StatCard = ({ label, value, sub, accent }) => (
  <div className={`bg-white rounded-xl border-l-4 ${accent} p-4 shadow-sm`}>
    <p className="text-xs text-gray-500 uppercase tracking-wider">{label}</p>
    <p className="text-3xl font-bold text-gray-800 mt-1">{value}</p>
    {sub && <p className="text-xs text-gray-400 mt-1">{sub}</p>}
  </div>
);

// ─── LOGIN PAGE ───────────────────────────────────────────
function LoginPage({ onLogin }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error,    setError]    = useState("");
  const [loading,  setLoading]  = useState(false);

  const handleLogin = async () => {
    if (!username || !password) {
      setError("Please enter username and password!");
      return;
    }
    setLoading(true);
    setError("");
    try {
      const r = await fetch(
        `${API_BASE}/api/admin/login`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ username, password })
      });
      const data = await r.json();
      if (data.success) {
        onLogin(username);
      } else {
        setError("Invalid username or password!");
      }
    } catch {
      setError(
        "Cannot connect to server! "
        + "Make sure server.py is running.");
    }
    setLoading(false);
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-indigo-900 to-indigo-700 flex items-center justify-center p-4">
      <div className="bg-white rounded-2xl shadow-2xl w-full max-w-md p-8">
        <div className="text-center mb-8">
          <div className="text-4xl mb-3">🔐</div>
          <h1 className="text-2xl font-bold text-gray-800">
            MFA Exam Security
          </h1>
          <p className="text-gray-400 text-sm mt-1">
            Admin Dashboard Login
          </p>
        </div>

        <div className="space-y-4">
          <div>
            <label className="block text-sm font-medium text-gray-600 mb-1">
              Username
            </label>
            <input
              type="text"
              value={username}
              onChange={e => setUsername(e.target.value)}
              placeholder="Enter username"
              className="w-full px-4 py-3 border border-gray-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500 text-sm"
            />
          </div>
          <div>
            <label className="block text-sm font-medium text-gray-600 mb-1">
              Password
            </label>
            <input
              type="password"
              value={password}
              onChange={e => setPassword(e.target.value)}
              onKeyDown={e => e.key === "Enter" && handleLogin()}
              placeholder="Enter password"
              className="w-full px-4 py-3 border border-gray-200 rounded-xl focus:outline-none focus:ring-2 focus:ring-indigo-500 text-sm"
            />
          </div>

          {error && (
            <div className="bg-red-50 border border-red-200 rounded-xl px-4 py-3 text-red-700 text-sm">
              {error}
            </div>
          )}

          <button
            onClick={handleLogin}
            disabled={loading}
            className="w-full bg-indigo-600 hover:bg-indigo-700 text-white font-semibold py-3 rounded-xl transition-all text-sm disabled:opacity-50"
          >
            {loading ? "Authenticating..." : "Login to Dashboard"}
          </button>
        </div>

        <p className="text-center text-xs text-gray-400 mt-6">
          MFA Exam Security System © 2026
        </p>
      </div>
    </div>
  );
}

// ─── STUDENTS TAB ─────────────────────────────────────────
function StudentsTab() {
  const [students, setStudents] = useState([]);
  const [loading,  setLoading]  = useState(false);
  const [form,     setForm]     = useState({
    roll_no: "", name: "", phone: "",
    assigned_room: "", seat_no: ""
  });
  const [msg, setMsg] = useState("");

  const loadStudents = async () => {
    try {
      const r    = await fetch(`${API_BASE}/admin/students`);
      const data = await r.json();
      setStudents(data);
    } catch {}
  };

  useEffect(() => { loadStudents(); }, []);

  const addStudent = async () => {
    if (!form.roll_no || !form.name) {
      setMsg("Roll number and name are required!");
      return;
    }
    try {
      const r = await fetch(
        `${API_BASE}/admin/students`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify(form)
      });
      const data = await r.json();
      if (data.status === "created") {
        setMsg(`Student ${form.name} added!`);
        setForm({
          roll_no: "", name: "", phone: "",
          assigned_room: "", seat_no: ""
        });
        loadStudents();
      }
    } catch {
      setMsg("Error adding student!");
    }
  };

  const downloadQR = async (roll_no) => {
    window.open(
      `${API_BASE}/admin/generate-qr/${roll_no}`,
      "_blank"
    );
  };

  const downloadHallTicket = async (roll_no) => {
    window.open(
      `${API_BASE}/admin/generate-hall-ticket/${roll_no}`,
      "_blank"
    );
  };

  return (
    <div className="space-y-6">
      {/* Add student form */}
      <div className="bg-white rounded-xl shadow-sm border p-5">
        <h3 className="font-semibold text-gray-700 mb-4">
          Add New Student
        </h3>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3">
          {[
            ["roll_no",       "Roll Number",  "22BCS001"],
            ["name",          "Full Name",    "Rahul Sharma"],
            ["phone",         "Phone",        "9876543210"],
            ["assigned_room", "Room",         "HALL-A"],
            ["seat_no",       "Seat No",      "1"],
          ].map(([key, label, ph]) => (
            <div key={key}>
              <label className="text-xs text-gray-500 block mb-1">
                {label}
              </label>
              <input
                type="text"
                value={form[key]}
                onChange={e => setForm({
                  ...form, [key]: e.target.value
                })}
                placeholder={ph}
                className="w-full px-3 py-2 border border-gray-200 rounded-lg text-sm focus:outline-none focus:ring-2 focus:ring-indigo-400"
              />
            </div>
          ))}
        </div>
        {msg && (
          <p className="text-sm mt-3 text-indigo-600">
            {msg}
          </p>
        )}
        <button
          onClick={addStudent}
          className="mt-4 bg-indigo-600 text-white px-5 py-2 rounded-lg text-sm font-medium hover:bg-indigo-700 transition"
        >
          Add Student
        </button>
      </div>

      {/* Students table */}
      <div className="bg-white rounded-xl shadow-sm border overflow-hidden">
        <div className="px-4 py-3 border-b flex justify-between items-center">
          <h3 className="font-semibold text-gray-700">
            All Students ({students.length})
          </h3>
          <button
            onClick={loadStudents}
            className="text-xs text-indigo-600 hover:underline"
          >
            Refresh
          </button>
        </div>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead className="bg-gray-50 border-b">
              <tr>
                {["Roll No", "Name", "Phone",
                  "Room", "Seat", "RFID",
                  "Face", "QR", "Hall Ticket"].map(h => (
                  <th key={h} className="px-3 py-2 text-left text-xs text-gray-500 font-semibold uppercase">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {students.length === 0 && (
                <tr>
                  <td colSpan={9} className="px-4 py-8 text-center text-gray-400">
                    No students found
                  </td>
                </tr>
              )}
              {students.map((s, i) => (
                <tr key={i} className="border-b hover:bg-gray-50">
                  <td className="px-3 py-2 font-mono text-xs text-gray-600">
                    {s.roll_no}
                  </td>
                  <td className="px-3 py-2 font-medium text-gray-800">
                    {s.name}
                  </td>
                  <td className="px-3 py-2 text-gray-500">
                    {s.phone || "—"}
                  </td>
                  <td className="px-3 py-2">
                    <Badge
                      text={s.assigned_room || "—"}
                      color={s.assigned_room ? "blue" : "gray"}
                    />
                  </td>
                  <td className="px-3 py-2 text-center text-gray-500">
                    {s.seat_no || "—"}
                  </td>
                  <td className="px-3 py-2">
                    <Badge
                      text={s.rfid_uid ? "Enrolled" : "Missing"}
                      color={s.rfid_uid ? "green" : "red"}
                    />
                  </td>
                  <td className="px-3 py-2">
                    <Badge
                      text={s.face_enrolled ? "Enrolled" : "Missing"}
                      color={s.face_enrolled ? "green" : "red"}
                    />
                  </td>
                  <td className="px-3 py-2">
                    <button
                      onClick={() => downloadQR(s.roll_no)}
                      className="text-xs text-indigo-600 hover:underline"
                    >
                      Download QR
                    </button>
                  </td>
                  <td className="px-3 py-2">
                    <button
                      onClick={() => downloadHallTicket(s.roll_no)}
                      className="text-xs bg-indigo-600 text-white px-2 py-1 rounded hover:bg-indigo-700 transition font-medium"
                    >
                      Hall Ticket
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

// ─── ROOMS TAB ────────────────────────────────────────────
function RoomsTab() {
  const [rooms,   setRooms]   = useState([]);

  const loadRooms = async () => {
    try {
      const r    = await fetch(`${API_BASE}/admin/rooms`);
      const data = await r.json();
      setRooms(data);
    } catch {}
  };

  useEffect(() => {
    loadRooms();
    const t = setInterval(loadRooms, POLL_MS);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
      {rooms.map((room, i) => (
        <div key={i} className={`bg-white rounded-xl shadow-sm border-2 p-5 ${
          room.is_active
            ? "border-green-400"
            : "border-gray-200"
        }`}>
          <div className="flex justify-between items-start mb-3">
            <h3 className="font-bold text-gray-800 text-lg">
              {room.room_id}
            </h3>
            <Badge
              text={room.is_active ? "Active" : "Inactive"}
              color={room.is_active ? "green" : "gray"}
            />
          </div>
          <p className="text-sm text-gray-500 mb-1">
            {room.subject || "No subject"}
          </p>
          <p className="text-sm text-gray-600">
            Faculty: <span className="font-medium">
              {room.faculty_name || "—"}
            </span>
          </p>
          {room.is_active && room.activated_at && (
            <p className="text-xs text-gray-400 mt-2">
              Activated:{" "}
              {new Date(room.activated_at)
                .toLocaleTimeString()}
            </p>
          )}
          {room.is_active && (
            <div className="mt-3 bg-yellow-50 border border-yellow-200 rounded-lg px-3 py-2">
              <p className="text-xs text-yellow-700 font-medium">
                Student emergency PIN
              </p>
              <p className="text-xl font-bold text-yellow-800 tracking-widest">
                {room.student_pin || "—"}
              </p>
            </div>
          )}
        </div>
      ))}
      {rooms.length === 0 && (
        <div className="col-span-3 text-center py-12 text-gray-400">
          No exam sessions configured for today
        </div>
      )}
    </div>
  );
}

// ─── MAIN DASHBOARD ───────────────────────────────────────
export default function AdminDashboard() {
  const [loggedIn,    setLoggedIn]    = useState(false);
  const [adminName,   setAdminName]   = useState("");
  const [activeRoom,  setActiveRoom]  = useState(ROOMS[0]);
  const [activeTab,   setActiveTab]   = useState("live");
  const [logs,        setLogs]        = useState([]);
  const [anomalies,   setAnomalies]   = useState([]);
  const [absentees,   setAbsentees]   = useState([]);
  const [lastRefresh, setLastRefresh] = useState(null);
  const [alarmFlash,  setAlarmFlash]  = useState(false);
  const [health,      setHealth]      = useState(null);

  // ── Auth check ──────────────────────────────────────────
  useEffect(() => {
    const auth = sessionStorage.getItem("admin_auth");
    const name = sessionStorage.getItem("admin_name");
    if (auth === "true") {
      setLoggedIn(true);
      setAdminName(name || "Admin");
    }
  }, []);

  const handleLogin = (username) => {
    sessionStorage.setItem("admin_auth", "true");
    sessionStorage.setItem("admin_name", username);
    setLoggedIn(true);
    setAdminName(username);
  };

  const handleLogout = () => {
    sessionStorage.removeItem("admin_auth");
    sessionStorage.removeItem("admin_name");
    setLoggedIn(false);
  };

  // ── Fetch health ────────────────────────────────────────
  const fetchHealth = useCallback(async () => {
    try {
      const r    = await fetch(`${API_BASE}/health`);
      const data = await r.json();
      setHealth(data);
    } catch {
      setHealth(null);
    }
  }, []);

  // ── Fetch logs ──────────────────────────────────────────
  const fetchLogs = useCallback(async () => {
    try {
      const r    = await fetch(
        `${API_BASE}/admin/logs/${activeRoom}?limit=100`);
      const data = await r.json();
      setLogs(data.logs || []);
      setLastRefresh(new Date().toLocaleTimeString());
    } catch {}
  }, [activeRoom]);

  // ── Fetch anomalies ─────────────────────────────────────
  const fetchAnomalies = useCallback(async () => {
    try {
      const r    = await fetch(
        `${API_BASE}/admin/anomalies/${activeRoom}`);
      const data = await r.json();
      const list = data.anomalies || [];
      if (list.length > anomalies.length) {
        setAlarmFlash(true);
        setTimeout(() => setAlarmFlash(false), 8000);
      }
      setAnomalies(list);
    } catch {}
  }, [activeRoom, anomalies.length]);

  // ── Fetch absentees ─────────────────────────────────────
  const fetchAbsentees = useCallback(async () => {
    try {
      const r    = await fetch(
        `${API_BASE}/admin/absentees/${activeRoom}`);
      const data = await r.json();
      setAbsentees(data.absentees || []);
    } catch {}
  }, [activeRoom]);

  // ── Poll ────────────────────────────────────────────────
  useEffect(() => {
    if (!loggedIn) return;
    fetchHealth();
    fetchLogs();
    fetchAnomalies();
    fetchAbsentees();
    const t = setInterval(() => {
      fetchHealth();
      fetchLogs();
      fetchAnomalies();
      if (activeTab === "absentees")
        fetchAbsentees();
    }, POLL_MS);
    return () => clearInterval(t);
  }, [loggedIn, activeRoom, activeTab,
      fetchHealth, fetchLogs,
      fetchAnomalies, fetchAbsentees]);

  // ── Stats ───────────────────────────────────────────────
  const entries   = logs.filter(l =>
    l.event_type === "entry" &&
    l.auth_status === "SUCCESS").length;
  const denials   = logs.filter(l =>
    l.auth_status === "DENY").length;
  const fallbacks = logs.filter(l =>
    l.fallbacks_used > 0).length;

  if (!loggedIn) {
    return <LoginPage onLogin={handleLogin} />;
  }

  return (
    <div className="min-h-screen bg-gray-50 font-sans">

      {/* Alarm banner */}
      {alarmFlash && (
        <div className="fixed top-0 left-0 right-0 z-50 bg-red-600 text-white text-center py-3 font-bold animate-pulse">
          UNAUTHORIZED ENTRY DETECTED IN {activeRoom} — CHECK ANOMALY LOG
        </div>
      )}

      {/* Header */}
      <div className="bg-gradient-to-r from-indigo-900 to-indigo-700 text-white px-6 py-4 shadow-md">
        <div className="max-w-7xl mx-auto flex items-center justify-between">
          <div>
            <h1 className="text-xl font-bold tracking-tight">
              MFA Exam Hall Security
            </h1>
            <p className="text-indigo-300 text-xs mt-0.5">
              Admin Dashboard · Live Monitor
            </p>
          </div>
          <div className="flex items-center gap-4">
            {health && (
              <div className="flex items-center gap-2 text-xs">
                <span className={`w-2 h-2 rounded-full ${health.mqtt ? "bg-green-400" : "bg-red-400"}`}/>
                <span className="text-indigo-200">
                  {health.mqtt ? "MQTT OK" : "MQTT Off"}
                </span>
                <span className={`w-2 h-2 rounded-full ${health.face_model ? "bg-green-400" : "bg-yellow-400"}`}/>
                <span className="text-indigo-200">
                  {health.face_model ? "Face OK" : "No Face Model"}
                </span>
              </div>
            )}
            <span className="text-indigo-300 text-xs">
              {adminName}
            </span>
            <span className="text-indigo-300 text-xs">
              Last refresh: {lastRefresh || "—"}
            </span>
            <div className="w-2 h-2 rounded-full bg-green-400 animate-pulse" />
            <button
              onClick={handleLogout}
              className="bg-red-500 hover:bg-red-600 text-white text-xs px-3 py-1.5 rounded-lg transition"
            >
              Logout
            </button>
          </div>
        </div>
      </div>

      <div className="max-w-7xl mx-auto px-4 py-6">

        {/* Room selector */}
        <div className="flex gap-2 mb-6 flex-wrap">
          {ROOMS.map(room => (
            <button
              key={room}
              onClick={() => setActiveRoom(room)}
              className={`px-5 py-2 rounded-full text-sm font-semibold border transition-all ${
                activeRoom === room
                  ? "bg-indigo-700 text-white border-indigo-700 shadow"
                  : "bg-white text-gray-600 border-gray-300 hover:border-indigo-400"
              }`}
            >
              {room}
            </button>
          ))}
          <div className="ml-auto flex items-center gap-1 text-xs text-gray-400 bg-white border rounded-full px-4 py-2">
            {new Date().toLocaleDateString("en-IN", {
              weekday: "short", day: "2-digit",
              month: "short", year: "numeric"
            })}
          </div>
        </div>

        {/* Stat cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
          <StatCard
            label="Students Entered"
            value={entries}
            sub="Successful entries today"
            accent="border-green-500"
          />
          <StatCard
            label="Denied Attempts"
            value={denials}
            sub="Access denied today"
            accent="border-red-500"
          />
          <StatCard
            label="Alarm Events"
            value={anomalies.length}
            sub="Unauthorized detections"
            accent="border-orange-500"
          />
          <StatCard
            label="Fallbacks Used"
            value={fallbacks}
            sub="Non-primary auth used"
            accent="border-yellow-500"
          />
        </div>

        {/* Tabs */}
        <div className="flex gap-1 bg-white border rounded-xl p-1 shadow-sm mb-5 w-fit flex-wrap">
          {[
            { id: "live",      label: "Live Log"   },
            { id: "anomalies", label: `Anomalies ${anomalies.length > 0 ? `(${anomalies.length})` : ""}` },
            { id: "absentees", label: `Absentees ${absentees.length > 0 ? `(${absentees.length})` : ""}` },
            { id: "students",  label: "Students"   },
            { id: "rooms",     label: "Rooms"      },
          ].map(tab => (
            <button
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
                activeTab === tab.id
                  ? "bg-indigo-700 text-white shadow"
                  : "text-gray-500 hover:text-gray-800"
              }`}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {/* ── LIVE LOG ── */}
        {activeTab === "live" && (
          <div className="bg-white rounded-xl shadow-sm border overflow-hidden">
            <div className="px-4 py-3 border-b flex items-center justify-between">
              <h2 className="font-semibold text-gray-700">
                Live Authentication Log — {activeRoom}
              </h2>
              <span className="text-xs text-gray-400">
                {logs.length} events
              </span>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b">
                  <tr>
                    {["Time", "Name", "Roll No", "Seat",
                      "Method", "Status", "Fallbacks",
                      "Entry", "Exit"].map(h => (
                      <th key={h} className="px-3 py-2 text-left text-xs text-gray-500 font-semibold uppercase">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {logs.length === 0 && (
                    <tr>
                      <td colSpan={9} className="px-4 py-10 text-center text-gray-400">
                        No events yet for {activeRoom} today
                      </td>
                    </tr>
                  )}
                  {logs.map((log, i) => (
                    <tr key={i} className={`border-b transition-colors ${
                      log.auth_status === "SUCCESS"
                        ? "hover:bg-green-50"
                        : log.auth_status === "DENY"
                        ? "hover:bg-red-50"
                        : "hover:bg-gray-50"
                    }`}>
                      <td className="px-3 py-2 text-gray-500 font-mono text-xs whitespace-nowrap">
                        {log.logged_at
                          ? new Date(log.logged_at)
                              .toLocaleTimeString()
                          : "—"}
                      </td>
                      <td className="px-3 py-2 font-medium text-gray-800">
                        {log.name || "—"}
                      </td>
                      <td className="px-3 py-2 text-gray-500 font-mono text-xs">
                        {log.roll_no || "—"}
                      </td>
                      <td className="px-3 py-2 text-gray-400 text-center">
                        {log.seat_no || "—"}
                      </td>
                      <td className="px-3 py-2">
                        <MethodBadge
                          method={log.auth_method}
                        />
                      </td>
                      <td className="px-3 py-2">
                        <Badge
                          text={log.auth_status}
                          color={
                            log.auth_status === "SUCCESS"
                              ? "green"
                              : log.auth_status === "DENY"
                              ? "red"
                              : "yellow"
                          }
                        />
                      </td>
                      <td className="px-3 py-2 text-center">
                        {log.fallbacks_used > 0
                          ? <Badge
                              text={`${log.fallbacks_used} fallback`}
                              color="yellow"
                            />
                          : <span className="text-gray-300">—</span>
                        }
                      </td>
                      <td className="px-3 py-2 text-gray-500 font-mono text-xs">
                        {log.entry_time
                          ? new Date(log.entry_time)
                              .toLocaleTimeString()
                          : "—"}
                      </td>
                      <td className="px-3 py-2 text-gray-500 font-mono text-xs">
                        {log.exit_time
                          ? new Date(log.exit_time)
                              .toLocaleTimeString()
                          : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {/* ── ANOMALIES ── */}
        {activeTab === "anomalies" && (
          <div className="space-y-3">
            {anomalies.length === 0 && (
              <div className="bg-white rounded-xl border p-10 text-center text-gray-400">
                No anomalies detected for {activeRoom} today
              </div>
            )}
            {anomalies.map((ev, i) => (
              <div key={i} className="bg-white rounded-xl border border-red-200 shadow-sm p-4 flex gap-4 items-start">
                <div className="w-10 h-10 rounded-full bg-red-100 flex items-center justify-center flex-shrink-0 text-xl">
                  !
                </div>
                <div className="flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <span className="font-semibold text-red-700">
                      {ev.event_type?.replace("_", " ")
                                     .toUpperCase()}
                    </span>
                    <Badge text={ev.room_id} color="red" />
                  </div>
                  <p className="text-gray-600 text-sm">
                    {ev.description || "No description"}
                  </p>
                  {ev.photo_path && (
                    <p className="text-xs text-gray-400 mt-1">
                      Photo: {ev.photo_path}
                    </p>
                  )}
                  <p className="text-xs text-gray-400 mt-1">
                    {ev.occurred_at
                      ? new Date(ev.occurred_at)
                          .toLocaleString()
                      : "—"}
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}

        {/* ── ABSENTEES ── */}
        {activeTab === "absentees" && (
          <div className="bg-white rounded-xl shadow-sm border overflow-hidden">
            <div className="px-4 py-3 border-b flex items-center justify-between">
              <h2 className="font-semibold text-gray-700">
                Absentee Students — {activeRoom}
              </h2>
              <span className="text-xs text-red-500 font-semibold">
                {absentees.length} absent
              </span>
            </div>
            <table className="w-full text-sm">
              <thead className="bg-gray-50 border-b">
                <tr>
                  {["Seat", "Roll No", "Name", "Status"].map(h => (
                    <th key={h} className="px-4 py-2 text-left text-xs text-gray-500 font-semibold uppercase">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {absentees.length === 0 && (
                  <tr>
                    <td colSpan={4} className="px-4 py-10 text-center text-gray-400">
                      All students have entered
                    </td>
                  </tr>
                )}
                {absentees.map((s, i) => (
                  <tr key={i} className="border-b hover:bg-yellow-50">
                    <td className="px-4 py-2 text-center font-mono text-gray-500">
                      {s.seat_no}
                    </td>
                    <td className="px-4 py-2 font-mono text-xs text-gray-600">
                      {s.roll_no}
                    </td>
                    <td className="px-4 py-2 font-medium text-gray-800">
                      {s.name}
                    </td>
                    <td className="px-4 py-2">
                      <Badge text="ABSENT" color="red" />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {absentees.length > 0 && (
              <div className="px-4 py-3 border-t bg-gray-50 flex justify-end">
                <button
                  onClick={() => {
                    const csv = [
                      "Seat,Roll No,Name",
                      ...absentees.map(s =>
                        `${s.seat_no},${s.roll_no},${s.name}`)
                    ].join("\n");
                    const a       = document.createElement("a");
                    a.href        = "data:text/csv,"
                                  + encodeURIComponent(csv);
                    a.download    =
                      `absentees_${activeRoom}_`
                      + `${new Date().toISOString().slice(0,10)}.csv`;
                    a.click();
                  }}
                  className="px-4 py-2 bg-indigo-600 text-white rounded-lg text-sm font-medium hover:bg-indigo-700 transition"
                >
                  Export CSV
                </button>
              </div>
            )}
          </div>
        )}

        {/* ── STUDENTS TAB ── */}
        {activeTab === "students" && (
          <StudentsTab />
        )}

        {/* ── ROOMS TAB ── */}
        {activeTab === "rooms" && (
          <RoomsTab />
        )}

      </div>
    </div>
  );
}