import {
  fetchAll,
  addFoodToSupabase,
  addMealToSupabase,
  addBodyLogToSupabase,
  deleteMealFromSupabase,
  deleteProductFromSupabase,
  updateProductInSupabase,
  updateMealInSupabase,
  updateDailyGoalsInSupabase,
  sbProductToFood,
  sbFoodLogToMeal,
  sbBodyToBodyLog,
  useSupabaseRealtime,
} from "./lib/supabaseSync";
import {
  Activity,
  Apple,
  BarChart3,
  Bell,
  CalendarDays,
  CheckCircle2,
  ChevronDown,
  CircleDollarSign,
  Dumbbell,
  Flame,
  Goal,
  HeartPulse,
  Home,
  Menu,
  Moon,
  Plus,
  Search,
  Settings,
  Smile,
  Sun,
  Sparkles,
  Target,
  TrendingUp,
  User,
  Utensils,
  Wallet,
  X,
} from "lucide-react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";

type Page =
  | "today"
  | "finance"
  | "nutrition"
  | "health"
  | "workouts"
  | "tasks"
  | "habits"
  | "goals"
  | "mood"
  | "journal"
  | "calendar"
  | "analytics"
  | "settings";
type Period = "Today" | "Week" | "Month" | "Year";
type TxType = "income" | "expense";
type Currency = "UAH" | "USD";
type TaskStatus = "today" | "upcoming" | "done";
type Mood = "focus" | "calm" | "good" | "low";

type Transaction = { id: string; date: string; time: string; title: string; category: string; type: TxType; amount: number; currency: Currency; personName?: string };
type FoodItem = { id: string; name: string; cal100: number; pro100: number; fat100: number; carb100: number };
type Meal = { id: string; date: string; name: string; mealType: string; calories: number; protein: number; fat: number; carbs: number; weight: number; foodId?: string };
type HealthLog = { id: string; date: string; sleep: number; water: number; mood: number };
type MoodEntry = { id: string; datetime: string; value: number; label: string };
type BodyLog = { id: string; date: string; weight: number; bmi: number; fatPct: number; musclePct: number; waterPct: number; boneMass: number; metabolism: number; proteinPct: number; bodyAge: number; visceralFat: number; fatKg: number; leanMass: number; muscleKg: number; proteinKg: number };
type Workout = { id: string; date: string; type: string; duration: number; calories: number; steps: number };
type Task = { id: string; title: string; status: TaskStatus; priority: "low" | "medium" | "high"; due: string };
type Habit = { id: string; title: string; streak: number; target: number; doneDates: string[] };
type GoalItem = { id: string; title: string; progress: number; targetDate: string; status: "on track" | "behind" | "completed"; linked: string };
type JournalEntry = { id: string; date: string; mood: Mood; text: string };
type EventItem = { id: string; date: string; title: string; time: string; type: string };
type SettingsData = { caloriesGoal: number | ""; proteinGoal: number | ""; fatGoal: number | ""; carbsGoal: number | ""; waterGoal: number | ""; sleepGoal: number | ""; monthlyBudget: number | ""; compactMode: boolean };
type AppData = {
  transactions: Transaction[];
  foods: FoodItem[];
  meals: Meal[];
  health: HealthLog[];
  bodyLogs: BodyLog[];
  workouts: Workout[];
  tasks: Task[];
  habits: Habit[];
  goals: GoalItem[];
  journal: JournalEntry[];
  events: EventItem[];
  settings: SettingsData;
  moodLog: MoodEntry[];
  expenseCategories: string[];
  incomeCategories: string[];
};

const today = new Date();
const iso = (offset = 0) => {
  const d = new Date(today);
  d.setDate(today.getDate() + offset);
  // Use local date parts to avoid UTC shift (fixes Kyiv/UTC+3 etc.)
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
};
// Returns "YYYY-MM-DDTHH:mm:ss" in the user's local timezone
const localDatetime = () => {
  const d = new Date();
  const y = d.getFullYear();
  const mo = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  const h = String(d.getHours()).padStart(2, "0");
  const min = String(d.getMinutes()).padStart(2, "0");
  const sec = String(d.getSeconds()).padStart(2, "0");
  return `${y}-${mo}-${day}T${h}:${min}:${sec}`;
};
// Converts any datetime string (local OR UTC) to local HH:MM
const fmtTime = (dt: string): string => {
  const d = new Date(dt);
  return `${String(d.getHours()).padStart(2, "0")}:${String(d.getMinutes()).padStart(2, "0")}`;
};
function periodStart(period: Period): string {
  if (period === "Today") return iso(0);
  if (period === "Week") { const d = new Date(today); d.setDate(today.getDate() - 6); return d.toISOString().slice(0, 10); }
  if (period === "Month") return `${iso(0).slice(0, 7)}-01`;
  return `${iso(0).slice(0, 4)}-01-01`;
}
const fmt = (cur: Currency, rate: number) => new Intl.NumberFormat("uk-UA", { style: "currency", currency: cur === "UAH" ? "UAH" : "USD", maximumFractionDigits: 0 });
const fmtAmt = (amount: number, cur: Currency, rate: number) =>
  new Intl.NumberFormat("uk-UA", { style: "currency", currency: cur === "UAH" ? "UAH" : "USD", maximumFractionDigits: cur === "UAH" ? 0 : 1 })
    .format(cur === "UAH" ? amount : amount / rate);
const money = new Intl.NumberFormat("uk-UA", { style: "currency", currency: "UAH", maximumFractionDigits: 0 });
const id = () => crypto.randomUUID();

const DEFAULT_EXPENSE_CATS = ["Продукты","Косметика","Заведения","Прочее","Техника","Подарки","Цветы","Кофе","Вредные привычки","Товары для дома","Аренда","Коммунальные","Массаж","Педикюр","Маникюр","Уход"];
const DEFAULT_INCOME_CATS = ["Презентация","Курсовая","Диплом","Экзамен","Проект","Статья","Эссе","Фриланс","Upwork","Трейдинг"];

const mockData: AppData = {
  transactions: [
    { id: id(), date: iso(-13), time: "14:22", title: "Курсовая — Максим", category: "Курсовая", type: "income", amount: 1200, currency: "UAH", personName: "Максим" },
    { id: id(), date: iso(-12), time: "10:05", title: "Продукты", category: "Продукты", type: "expense", amount: 380, currency: "UAH" },
    { id: id(), date: iso(-11), time: "18:40", title: "Диплом — Аня", category: "Диплом", type: "income", amount: 3500, currency: "UAH", personName: "Аня" },
    { id: id(), date: iso(-9),  time: "20:15", title: "Заведение", category: "Заведения", type: "expense", amount: 420, currency: "UAH" },
    { id: id(), date: iso(-8),  time: "09:00", title: "Аренда", category: "Аренда", type: "expense", amount: 8000, currency: "UAH" },
    { id: id(), date: iso(-7),  time: "16:33", title: "Презентация — Дима", category: "Презентация", type: "income", amount: 800, currency: "UAH", personName: "Дима" },
    { id: id(), date: iso(-6),  time: "11:18", title: "Кофе", category: "Кофе", type: "expense", amount: 95, currency: "UAH" },
    { id: id(), date: iso(-5),  time: "13:50", title: "Маникюр", category: "Маникюр", type: "expense", amount: 450, currency: "UAH" },
    { id: id(), date: iso(-4),  time: "19:07", title: "Эссе — Ольга", category: "Эссе", type: "income", amount: 600, currency: "UAH", personName: "Ольга" },
    { id: id(), date: iso(-3),  time: "10:42", title: "Продукты", category: "Продукты", type: "expense", amount: 290, currency: "UAH" },
    { id: id(), date: iso(-2),  time: "08:55", title: "Коммунальные", category: "Коммунальные", type: "expense", amount: 1200, currency: "UAH" },
    { id: id(), date: iso(-1),  time: "11:30", title: "Кофе", category: "Кофе", type: "expense", amount: 85, currency: "UAH" },
    { id: id(), date: iso(0),   time: "13:15", title: "Обед", category: "Заведения", type: "expense", amount: 180, currency: "UAH" },
  ],
  foods: [
    { id: id(), name: "Лаваш армянский", cal100: 277, pro100: 8.5, fat100: 2.1, carb100: 57 },
    { id: id(), name: "Куриная грудка", cal100: 165, pro100: 31, fat100: 3.6, carb100: 0 },
    { id: id(), name: "Гречка варёная", cal100: 132, pro100: 4.5, fat100: 1.1, carb100: 25.1 },
    { id: id(), name: "Яйцо куриное", cal100: 157, pro100: 12.7, fat100: 11.5, carb100: 0.7 },
    { id: id(), name: "Творог 5%", cal100: 121, pro100: 17, fat100: 5, carb100: 3 },
    { id: id(), name: "Овсянка", cal100: 366, pro100: 13, fat100: 6.9, carb100: 59.5 },
    { id: id(), name: "Греческий йогурт", cal100: 97, pro100: 9, fat100: 5, carb100: 3.6 },
    { id: id(), name: "Рис варёный", cal100: 130, pro100: 2.7, fat100: 0.3, carb100: 28.2 },
  ],
  meals: [
    { id: id(), date: iso(0), name: "Греческий йогурт", mealType: "Завтрак", calories: 194, protein: 18, fat: 10, carbs: 7, weight: 200 },
    { id: id(), date: iso(0), name: "Куриная грудка", mealType: "Обед", calories: 248, protein: 46, fat: 5, carbs: 0, weight: 150 },
    { id: id(), date: iso(-1), name: "Гречка варёная", mealType: "Ужин", calories: 264, protein: 9, fat: 2, carbs: 50, weight: 200 },
    { id: id(), date: iso(-2), name: "Лаваш армянский", mealType: "Завтрак", calories: 249, protein: 8, fat: 2, carbs: 51, weight: 90 },
    { id: id(), date: iso(-3), name: "Овсянка", mealType: "Завтрак", calories: 293, protein: 10, fat: 6, carbs: 48, weight: 80 },
  ],
  health: [
    { id: id(), date: iso(-6), sleep: 7.1, water: 2.4, mood: 7 },
    { id: id(), date: iso(-5), sleep: 6.7, water: 2.1, mood: 6 },
    { id: id(), date: iso(-4), sleep: 7.8, water: 2.7, mood: 8 },
    { id: id(), date: iso(-3), sleep: 6.2, water: 1.8, mood: 6 },
    { id: id(), date: iso(-2), sleep: 7.4, water: 2.6, mood: 8 },
    { id: id(), date: iso(-1), sleep: 7.0, water: 2.2, mood: 7 },
    { id: id(), date: iso(0), sleep: 7.6, water: 1.9, mood: 8 },
  ],
  bodyLogs: [
    { id: id(), date: iso(-2), weight: 78.1, bmi: 25.2, fatPct: 24.8, musclePct: 32.4, waterPct: 52.9, boneMass: 3.1, metabolism: 1838, proteinPct: 15.8, bodyAge: 31, visceralFat: 8, fatKg: 19.4, leanMass: 58.7, muscleKg: 25.3, proteinKg: 12.3 },
    { id: id(), date: iso(-1), weight: 77.8, bmi: 25.1, fatPct: 24.6, musclePct: 32.6, waterPct: 53.0, boneMass: 3.1, metabolism: 1842, proteinPct: 15.9, bodyAge: 30, visceralFat: 8, fatKg: 19.1, leanMass: 58.7, muscleKg: 25.4, proteinKg: 12.4 },
    { id: id(), date: iso(0), weight: 77.6, bmi: 25.0, fatPct: 24.4, musclePct: 32.7, waterPct: 53.2, boneMass: 3.1, metabolism: 1845, proteinPct: 16.0, bodyAge: 30, visceralFat: 8, fatKg: 18.9, leanMass: 58.7, muscleKg: 25.4, proteinKg: 12.4 },
  ],
  workouts: [
    { id: id(), date: iso(-5), type: "Strength", duration: 52, calories: 420, steps: 8200 },
    { id: id(), date: iso(-4), type: "Run", duration: 34, calories: 360, steps: 10400 },
    { id: id(), date: iso(-2), type: "Mobility", duration: 28, calories: 140, steps: 7600 },
    { id: id(), date: iso(-1), type: "Strength", duration: 58, calories: 460, steps: 9100 },
    { id: id(), date: iso(0), type: "Walk", duration: 42, calories: 220, steps: 6800 },
  ],
  tasks: [
    { id: id(), title: "Review weekly plan", status: "today", priority: "high", due: iso(0) },
    { id: id(), title: "Record finance recap", status: "today", priority: "medium", due: iso(0) },
    { id: id(), title: "Meal prep protein snacks", status: "upcoming", priority: "medium", due: iso(1) },
    { id: id(), title: "Publish long-form note", status: "upcoming", priority: "high", due: iso(2) },
    { id: id(), title: "Morning mobility", status: "done", priority: "low", due: iso(0) },
  ],
  habits: [
    { id: id(), title: "Water 2.5L", streak: 8, target: 7, doneDates: [iso(-5), iso(-4), iso(-3), iso(-2), iso(-1)] },
    { id: id(), title: "Deep work", streak: 12, target: 7, doneDates: [iso(-6), iso(-5), iso(-4), iso(-3), iso(-1), iso(0)] },
    { id: id(), title: "Reading", streak: 5, target: 7, doneDates: [iso(-4), iso(-3), iso(-2), iso(0)] },
    { id: id(), title: "Training", streak: 3, target: 4, doneDates: [iso(-5), iso(-2), iso(-1)] },
  ],
  goals: [
    { id: id(), title: "Build Life OS prototype", progress: 68, targetDate: iso(14), status: "on track", linked: "Product sprint" },
    { id: id(), title: "Save $18k runway", progress: 54, targetDate: iso(90), status: "on track", linked: "Budget habit" },
    { id: id(), title: "Reach 76 kg", progress: 42, targetDate: iso(60), status: "behind", linked: "Training + meals" },
  ],
  journal: [
    { id: id(), date: iso(0), mood: "focus", text: "Clear morning. Best energy before lunch, keep the first block protected." },
    { id: id(), date: iso(-1), mood: "good", text: "Strong workout and better food choices. Need earlier shutdown." },
    { id: id(), date: iso(-3), mood: "calm", text: "Good planning session, less context switching." },
  ],
  events: [
    { id: id(), date: iso(0), title: "Strategy review", time: "11:00", type: "Work" },
    { id: id(), date: iso(1), title: "Leg day", time: "18:30", type: "Fitness" },
    { id: id(), date: iso(3), title: "Dinner with Alex", time: "20:00", type: "Personal" },
    { id: id(), date: iso(6), title: "Monthly finance close", time: "10:00", type: "Finance" },
  ],
  settings: { caloriesGoal: 2200, proteinGoal: 150, fatGoal: 70, carbsGoal: 250, waterGoal: 2.5, sleepGoal: 7.5, monthlyBudget: 15000, compactMode: false },
  moodLog: [
    { id: id(), datetime: `${iso(0)}T09:15:00`, value: 5, label: "Хорошо" },
    { id: id(), datetime: `${iso(0)}T12:00:00`, value: 7, label: "Отлично" },
    { id: id(), datetime: `${iso(0)}T16:30:00`, value: 9, label: "Огонь" },
    { id: id(), datetime: `${iso(-1)}T08:30:00`, value: 3, label: "Норм" },
    { id: id(), datetime: `${iso(-1)}T13:00:00`, value: 5, label: "Хорошо" },
    { id: id(), datetime: `${iso(-1)}T19:00:00`, value: 7, label: "Отлично" },
    { id: id(), datetime: `${iso(-2)}T09:00:00`, value: 1, label: "Устал" },
    { id: id(), datetime: `${iso(-2)}T14:00:00`, value: 3, label: "Норм" },
    { id: id(), datetime: `${iso(-2)}T21:00:00`, value: 5, label: "Хорошо" },
    { id: id(), datetime: `${iso(-3)}T10:00:00`, value: 7, label: "Отлично" },
    { id: id(), datetime: `${iso(-3)}T15:00:00`, value: 9, label: "Огонь" },
    { id: id(), datetime: `${iso(-4)}T09:00:00`, value: 5, label: "Хорошо" },
    { id: id(), datetime: `${iso(-4)}T14:00:00`, value: 5, label: "Хорошо" },
    { id: id(), datetime: `${iso(-4)}T20:00:00`, value: 7, label: "Отлично" },
    { id: id(), datetime: `${iso(-5)}T08:00:00`, value: 3, label: "Норм" },
    { id: id(), datetime: `${iso(-5)}T16:00:00`, value: 5, label: "Хорошо" },
    { id: id(), datetime: `${iso(-5)}T22:00:00`, value: 7, label: "Отлично" },
    { id: id(), datetime: `${iso(-6)}T09:00:00`, value: 7, label: "Отлично" },
    { id: id(), datetime: `${iso(-6)}T12:00:00`, value: 7, label: "Отлично" },
    { id: id(), datetime: `${iso(-6)}T18:00:00`, value: 9, label: "Огонь" },
    { id: id(), datetime: `${iso(-7)}T10:00:00`, value: 1, label: "Устал" },
    { id: id(), datetime: `${iso(-7)}T15:00:00`, value: 3, label: "Норм" },
    { id: id(), datetime: `${iso(-7)}T20:00:00`, value: 5, label: "Хорошо" },
    { id: id(), datetime: `${iso(-8)}T09:00:00`, value: 5, label: "Хорошо" },
    { id: id(), datetime: `${iso(-8)}T17:00:00`, value: 7, label: "Отлично" },
    { id: id(), datetime: `${iso(-9)}T08:00:00`, value: 9, label: "Огонь" },
    { id: id(), datetime: `${iso(-9)}T14:00:00`, value: 7, label: "Отлично" },
    { id: id(), datetime: `${iso(-10)}T09:00:00`, value: 3, label: "Норм" },
    { id: id(), datetime: `${iso(-10)}T17:00:00`, value: 5, label: "Хорошо" },
    { id: id(), datetime: `${iso(-11)}T10:00:00`, value: 5, label: "Хорошо" },
    { id: id(), datetime: `${iso(-11)}T20:00:00`, value: 7, label: "Отлично" },
    { id: id(), datetime: `${iso(-12)}T09:00:00`, value: 7, label: "Отлично" },
    { id: id(), datetime: `${iso(-13)}T14:00:00`, value: 9, label: "Огонь" },
    { id: id(), datetime: `${iso(-14)}T09:00:00`, value: 1, label: "Устал" },
    { id: id(), datetime: `${iso(-14)}T15:00:00`, value: 3, label: "Норм" },
    { id: id(), datetime: `${iso(-14)}T21:00:00`, value: 5, label: "Хорошо" },
    { id: id(), datetime: `${iso(-15)}T10:00:00`, value: 5, label: "Хорошо" },
    { id: id(), datetime: `${iso(-16)}T09:00:00`, value: 7, label: "Отлично" },
    { id: id(), datetime: `${iso(-16)}T18:00:00`, value: 9, label: "Огонь" },
    { id: id(), datetime: `${iso(-17)}T08:00:00`, value: 3, label: "Норм" },
    { id: id(), datetime: `${iso(-18)}T09:00:00`, value: 5, label: "Хорошо" },
    { id: id(), datetime: `${iso(-19)}T14:00:00`, value: 7, label: "Отлично" },
    { id: id(), datetime: `${iso(-20)}T10:00:00`, value: 9, label: "Огонь" },
  ],
  expenseCategories: DEFAULT_EXPENSE_CATS,
  incomeCategories: DEFAULT_INCOME_CATS,
};

const STORAGE_KEY = "life-os-demo";

function loadData(): AppData {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (!saved) return mockData;
    const parsed = JSON.parse(saved) as Partial<AppData>;
    // Migrate: fill in new fields that didn't exist in older saved data
    return {
      ...mockData,
      ...parsed,
      transactions: (parsed.transactions ?? mockData.transactions).map((t) => ({ ...t, time: t.time ?? "00:00" })),
      foods: parsed.foods ?? mockData.foods,
      bodyLogs: parsed.bodyLogs ?? mockData.bodyLogs,
      moodLog: parsed.moodLog ?? mockData.moodLog,
      expenseCategories: parsed.expenseCategories ?? DEFAULT_EXPENSE_CATS,
      incomeCategories: parsed.incomeCategories ?? DEFAULT_INCOME_CATS,
      health: (parsed.health ?? mockData.health).map((h) => ({
        id: h.id, date: h.date,
        sleep: h.sleep ?? 0,
        water: h.water ?? 0,
        mood: h.mood ?? 5,
      })),
    };
  } catch {
    return mockData;
  }
}

const nav = [
  ["today", Home, "Сегодня"],
  ["finance", Wallet, "Финансы"],
  ["nutrition", Apple, "Питание"],
  ["health", HeartPulse, "Здоровье"],
  ["workouts", Dumbbell, "Тренировки"],
  ["tasks", CheckCircle2, "Задачи"],
  ["habits", Flame, "Привычки"],
  ["goals", Goal, "Цели"],
  ["mood", Smile, "Настроение"],
  ["journal", Sparkles, "Журнал"],
  ["calendar", CalendarDays, "Календарь"],
  ["analytics", BarChart3, "Аналитика"],
  ["settings", Settings, "Настройки"],
] as const;

// Расчёт целей БЖУ от калорий: Б 30% / Ж 25% / У 45%
function deriveMacroGoals(calories: number) {
  return {
    protein: Math.round((calories * 0.30) / 4),
    fat: Math.round((calories * 0.25) / 9),
    carbs: Math.round((calories * 0.45) / 4),
  };
}

function App() {
  const [page, setPage] = useState<Page>("today");
  const [period, setPeriod] = useState<Period>("Today");
  const [data, setData] = useState<AppData>(loadData);
  const [quick, setQuick] = useState<null | string>(null);
  const [menuOpen, setMenuOpen] = useState(false);
  const [commandOpen, setCommandOpen] = useState(false);
  const [eventModal, setEventModal] = useState<EventItem | null>(null);
  const [dark, setDark] = useState(() => localStorage.getItem("life-os-theme") === "dark");

  useEffect(() => {
    document.documentElement.classList.toggle("dark", dark);
  }, [dark]);

  const toggleDark = () => setDark((d) => {
    const next = !d;
    localStorage.setItem("life-os-theme", next ? "dark" : "light");
    return next;
  });

  useEffect(() => localStorage.setItem(STORAGE_KEY, JSON.stringify(data)), [data]);

  // ── Supabase sync ──────────────────────────────────────────────
  useEffect(() => {
    fetchAll().then(({ products, foodLog, bodyMeasurements, dailyGoals }) => {
      setData((prev) => {
        // Цель калорий: ручная (daily_goals) > обмен веществ с последнего взвешивания (bmr) > прежняя
        const latestBmr = bodyMeasurements.find((b: any) => b.bmr)?.bmr;
        const calGoal = dailyGoals?.calories ?? latestBmr ?? prev.settings.caloriesGoal;
        // БЖУ: ручные > расчёт от калорий (Б 30% / Ж 25% / У 45%)
        const derived = deriveMacroGoals(Number(calGoal) || 0);
        return {
          ...prev,
          foods: products.length > 0 ? products.map(sbProductToFood) : prev.foods,
          meals: foodLog.length > 0 ? foodLog.map(sbFoodLogToMeal) : prev.meals,
          bodyLogs: bodyMeasurements.length > 0 ? bodyMeasurements.map(sbBodyToBodyLog) : prev.bodyLogs,
          settings: {
            ...prev.settings,
            caloriesGoal: calGoal,
            proteinGoal: dailyGoals?.protein ?? derived.protein,
            fatGoal: dailyGoals?.fat ?? derived.fat,
            carbsGoal: dailyGoals?.carbs ?? derived.carbs,
          },
        };
      });
    });
  }, []);

  const handleRealtimeUpdate = useCallback((table: string, row: any) => {
    if (table === 'food_log') {
      setData((prev) => {
        const exists = prev.meals.some((m) => m.id === row.id);
        if (exists) return prev;
        return { ...prev, meals: [sbFoodLogToMeal(row), ...prev.meals] };
      });
    } else if (table === 'products') {
      setData((prev) => {
        const exists = prev.foods.some((f) => f.id === row.id);
        if (exists) return prev;
        return { ...prev, foods: [sbProductToFood(row), ...prev.foods] };
      });
    } else if (table === 'body_measurements' || table === 'body_measurements_update') {
      setData((prev) => {
        const filtered = prev.bodyLogs.filter((b) => b.id !== row.id);
        // Новый скриншот взвешивания → обновляем цель калорий из обмена веществ (bmr)
        const dm = deriveMacroGoals(Number(row.bmr) || 0);
        const nextSettings = row.bmr
          ? { ...prev.settings, caloriesGoal: row.bmr, proteinGoal: dm.protein, fatGoal: dm.fat, carbsGoal: dm.carbs }
          : prev.settings;
        return { ...prev, bodyLogs: [sbBodyToBodyLog(row), ...filtered], settings: nextSettings };
      });
    } else if (table === 'daily_goals') {
      setData((prev) => ({
        ...prev,
        settings: {
          ...prev.settings,
          caloriesGoal: row.calories ?? prev.settings.caloriesGoal,
          proteinGoal: row.protein ?? prev.settings.proteinGoal,
          fatGoal: row.fat ?? prev.settings.fatGoal,
          carbsGoal: row.carbs ?? prev.settings.carbsGoal,
        },
      }));
    }
  }, []);

  useSupabaseRealtime(handleRealtimeUpdate);

  const title = nav.find(([key]) => key === page)?.[2] ?? "Life OS";
  const add = <T extends keyof AppData>(key: T, item: AppData[T] extends Array<infer U> ? U : never) =>
    setData((prev) => ({ ...prev, [key]: [item, ...(prev[key] as unknown[])] }));

  const metrics = useMemo(() => getMetrics(data, period), [data, period]);

  return (
    <div className={`premium-bg min-h-screen text-ink${dark ? " dark" : ""}`}>
      <Sidebar page={page} setPage={setPage} open={menuOpen} close={() => setMenuOpen(false)} />
      <div className="lg:pl-72">
        <header className="premium-header sticky top-0 z-30">
          <div className="flex h-20 items-center gap-3 px-4 sm:px-6 lg:px-8">
            <button className="icon-btn lg:hidden" onClick={() => setMenuOpen(true)} aria-label="Открыть меню">
              <Menu size={20} />
            </button>
            <div className="min-w-0 flex-1">
              <p className="text-xs font-medium uppercase tracking-wide text-slate-400">Personal command center</p>
              <h1 className="truncate text-2xl font-semibold">{title}</h1>
            </div>
            <button className="search-shell hidden min-w-72 md:flex" onClick={() => setCommandOpen(true)}>
              <Search size={17} />
              <span>Найти запись, задачу или действие...</span>
            </button>
            <div className="hidden rounded-full border border-[var(--border)] bg-[var(--glass-heavy)] p-1 shadow-soft sm:flex">
              {(["Today", "Week", "Month", "Year"] as Period[]).map((item) => (
                <button key={item} onClick={() => setPeriod(item)} className={`period ${period === item ? "active" : ""}`}>
                  {item}
                </button>
              ))}
            </div>
            <button className="primary-btn" onClick={() => setQuick("menu")}>
              <Plus size={18} />
              <span className="hidden sm:inline">Добавить</span>
            </button>
            <button className="icon-btn" aria-label="Уведомления">
              <Bell size={19} />
            </button>
            <button className="icon-btn theme-toggle" onClick={toggleDark} aria-label={dark ? "Светлая тема" : "Тёмная тема"} title={dark ? "Светлая тема" : "Тёмная тема"}>
              {dark ? <Sun size={18} /> : <Moon size={18} />}
            </button>
            <button className="icon-btn" onClick={() => setPage("settings")} aria-label="Профиль">
              <User size={19} />
            </button>
          </div>
        </header>
        <main className="px-4 py-6 pb-24 sm:px-6 lg:px-8 lg:pb-10">
          <div className={page === "today" ? "premium-stage" : "premium-stage premium-stage-compact"}>
          {page === "today" && <Today data={data} metrics={metrics} setData={setData} setPage={setPage} openEvent={setEventModal} period={period} />}
          {page === "finance" && <Finance data={data} add={add} setData={setData} />}
          {page === "nutrition" && <Nutrition data={data} add={add} setData={setData} />}
          {page === "health" && <Health data={data} add={add} setData={setData} />}
          {page === "workouts" && <Workouts data={data} add={add} />}
          {page === "tasks" && <Tasks data={data} setData={setData} add={add} />}
          {page === "habits" && <Habits data={data} setData={setData} add={add} />}
          {page === "goals" && <Goals data={data} add={add} />}
          {page === "mood" && <MoodPage data={data} setData={setData} />}
          {page === "journal" && <Journal data={data} add={add} />}
          {page === "calendar" && <CalendarPage data={data} add={add} openEvent={setEventModal} />}
          {page === "analytics" && <Analytics data={data} />}
          {page === "settings" && <SettingsPage data={data} setData={setData} />}
          </div>
        </main>
      </div>
      <MobileNav page={page} setPage={setPage} />
      {quick && (
        <QuickAdd
          mode={quick}
          setMode={setQuick}
          add={add}
          close={() => setQuick(null)}
        />
      )}
      {commandOpen && <CommandModal close={() => setCommandOpen(false)} setPage={setPage} />}
      {eventModal && <EventModal event={eventModal} close={() => setEventModal(null)} />}
    </div>
  );
}

function Sidebar({ page, setPage, open, close }: { page: Page; setPage: (p: Page) => void; open: boolean; close: () => void }) {
  return (
    <>
      <div className={`fixed inset-0 z-40 bg-black/20 lg:hidden ${open ? "" : "hidden"}`} onClick={close} />
      <aside className={`premium-sidebar fixed inset-y-0 left-0 z-50 flex w-72 flex-col px-4 py-5 transition lg:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"}`}>
        <div className="mb-7 flex items-center justify-between">
          <button className="flex items-center gap-3 text-left" onClick={() => { setPage("today"); close(); }}>
            <div className="brand-mark grid h-10 w-10 place-items-center rounded-xl text-white shadow-soft">
              <Activity size={21} />
            </div>
            <div>
              <div className="text-lg font-semibold">Life OS</div>
              <div className="text-xs text-slate-400">жизнь.os</div>
            </div>
          </button>
          <button className="icon-btn lg:hidden" onClick={close}><X size={18} /></button>
        </div>
        <nav className="min-h-0 flex-1 space-y-1 overflow-y-auto">
          {nav.map(([key, Icon, label]) => (
            <button
              key={key}
              onClick={() => { setPage(key); close(); }}
              className={`nav-item ${page === key ? "nav-active" : ""}`}
            >
              <Icon size={19} />
              <span>{label}</span>
            </button>
          ))}
        </nav>
        <div className="profile-card mt-4 rounded-2xl p-3">
          <div className="flex items-center gap-3">
            <div className="grid h-10 w-10 place-items-center rounded-full bg-[var(--glass-heavy)] text-[var(--accent)] border border-[var(--border)] font-bold shadow-soft">M</div>
            <div className="min-w-0">
              <p className="truncate text-sm font-semibold">My personal OS</p>
              <p className="truncate text-xs text-slate-500">Demo workspace</p>
            </div>
          </div>
        </div>
      </aside>
    </>
  );
}

function MobileNav({ page, setPage }: { page: Page; setPage: (p: Page) => void }) {
  const items = nav.slice(0, 5);
  return (
    <div className="fixed inset-x-0 bottom-0 z-30 border-t border-slate-200 bg-white/95 p-2 backdrop-blur lg:hidden">
      <div className="grid grid-cols-5 gap-1">
        {items.map(([key, Icon, label]) => (
          <button key={key} onClick={() => setPage(key)} className={`mobile-nav ${page === key ? "mobile-active" : ""}`}>
            <Icon size={18} />
            <span>{label}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function getMetrics(data: AppData, period: Period = "Today") {
  const start = periodStart(period);
  const end = iso(0);
  const inRange = (date: string) => date >= start && date <= end;

  // Balance is always total (all time)
  const totalIncome = data.transactions.filter((t) => t.type === "income").reduce((s, t) => s + t.amount, 0);
  const totalExpenses = data.transactions.filter((t) => t.type === "expense").reduce((s, t) => s + t.amount, 0);
  const balance = totalIncome - totalExpenses;

  // Period-filtered income & expenses
  const periodTx = data.transactions.filter((t) => inRange(t.date));
  const income = periodTx.filter((t) => t.type === "income").reduce((s, t) => s + t.amount, 0);
  const expenses = periodTx.filter((t) => t.type === "expense").reduce((s, t) => s + t.amount, 0);

  // Calories: today if Today, else avg per day in period
  const todayMeals = data.meals.filter((m) => m.date === end);
  const periodMeals = data.meals.filter((m) => inRange(m.date));
  const uniqueDays = new Set(periodMeals.map((m) => m.date)).size;
  const calories = period === "Today"
    ? todayMeals.reduce((s, m) => s + m.calories, 0)
    : Math.round(periodMeals.reduce((s, m) => s + m.calories, 0) / Math.max(uniqueDays, 1));
  const macros = todayMeals.reduce((a, m) => ({ protein: a.protein + m.protein, fat: a.fat + m.fat, carbs: a.carbs + m.carbs }), { protein: 0, fat: 0, carbs: 0 });

  const latestHealth = [...data.health].sort((a, b) => b.date.localeCompare(a.date))[0];
  const latestBody = [...(data.bodyLogs ?? [])].sort((a, b) => b.date.localeCompare(a.date))[0];
  const doneHabits = data.habits.filter((h) => h.doneDates.includes(end)).length;
  const activeTasks = data.tasks.filter((t) => t.status !== "done" && (period === "Today" ? t.due === end : inRange(t.due))).length;
  return { balance, income, expenses, calories, macros, latestHealth, latestBody, doneHabits, activeTasks };
}

function chart7<T extends { date: string }>(items: T[], mapper: (items: T[]) => Record<string, number>) {
  return Array.from({ length: 7 }, (_, i) => {
    const date = iso(i - 6);
    return { date: date.slice(5), ...mapper(items.filter((x) => x.date === date)) };
  });
}

function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return <section className={`card ${className}`}>{children}</section>;
}

function Kpi({ title, value, sub, icon: Icon, tone = "orange" }: { title: string; value: string; sub: string; icon: typeof Activity; tone?: string }) {
  return (
    <Card>
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-sm text-slate-500">{title}</p>
          <p className="mt-2 text-2xl font-semibold">{value}</p>
          <p className="mt-1 text-xs text-slate-400">{sub}</p>
        </div>
        <div className={`kpi-icon ${tone}`}><Icon size={19} /></div>
      </div>
    </Card>
  );
}

function SectionTitle({ title, sub }: { title: string; sub?: React.ReactNode }) {
  return (
    <div className="mb-4 flex items-end justify-between gap-3">
      <div>
        <h2 className="text-base font-semibold">{title}</h2>
        {sub && (typeof sub === "string" ? <p className="text-sm text-slate-500">{sub}</p> : <div>{sub}</div>)}
      </div>
    </div>
  );
}

function Progress({ value, color = "bg-accent" }: { value: number; color?: string }) {
  return <div className="h-2.5 rounded-full bg-slate-100"><div className={`h-full rounded-full ${color}`} style={{ width: `${Math.max(0, Math.min(value, 100))}%` }} /></div>;
}

const MOOD_EMOJIS: [number, string, string][] = [[1, "😴", "Устал"], [3, "😐", "Норм"], [5, "🙂", "Хорошо"], [7, "😄", "Отлично"], [9, "🔥", "Огонь"]];

function Today({ data, metrics, setData, setPage, openEvent, period }: { data: AppData; metrics: ReturnType<typeof getMetrics>; setData: React.Dispatch<React.SetStateAction<AppData>>; setPage: (p: Page) => void; openEvent: (e: EventItem) => void; period: Period }) {
  const finance = chart7(data.transactions, (xs) => ({ income: xs.filter((x) => x.type === "income").reduce((s, x) => s + x.amount, 0), expense: xs.filter((x) => x.type === "expense").reduce((s, x) => s + x.amount, 0) })) as Array<{ date: string; income: number; expense: number }>;
  const mood = chart7(data.health, (xs) => ({ mood: xs[0]?.mood ?? 6 })) as Array<{ date: string; mood: number }>;
  const latestMeals = data.meals.filter((m) => m.date === iso(0));
  const spendingBars = finance.map((d) => Math.max(14, Math.min(96, Number(d.expense) / 12 + 28)));

  const PERIOD_RU: Record<Period, string> = { Today: "сегодня", Week: "за неделю", Month: "за месяц", Year: "за год" };
  const periodLabel = PERIOD_RU[period];
  const isAvg = period !== "Today";

  const todayTasks = period === "Today"
    ? data.tasks.filter((t) => t.status !== "done" && t.due === iso(0))
    : data.tasks.filter((t) => t.status !== "done");

  const moodLog = data.moodLog ?? [];
  const lastMoodEntry = moodLog[0] ?? null;
  const todayMoodEntries = moodLog.filter((e) => e.datetime.startsWith(iso(0)));
  const currentMood = lastMoodEntry?.value ?? 0;

  const handleMoodLog = (val: number) => {
    const moodLabel = MOOD_EMOJIS.find(([v]) => v === val)?.[2] ?? "Норм";
    const entry: MoodEntry = {
      id: id(),
      datetime: localDatetime(),
      value: val,
      label: moodLabel,
    };
    setData((p) => ({ ...p, moodLog: [entry, ...(p.moodLog ?? [])] }));
  };

  const todayMoodAvg = todayMoodEntries.length > 0
    ? Math.round((todayMoodEntries.reduce((s, e) => s + e.value, 0) / todayMoodEntries.length) * 10) / 10
    : currentMood;

  const circleRadius = 13;
  const circleCircumference = 2 * Math.PI * circleRadius;
  const strokeOffset = circleCircumference - (todayMoodAvg / 9) * circleCircumference;
  const circleColor = MOOD_COLOR(Math.round(todayMoodAvg));

  const toggleHabit = (habit: Habit) => {
    const done = habit.doneDates.includes(iso(0));
    setData((p) => ({ ...p, habits: p.habits.map((h) => h.id === habit.id ? { ...h, doneDates: done ? h.doneDates.filter((d) => d !== iso(0)) : [...h.doneDates, iso(0)], streak: done ? Math.max(0, h.streak - 1) : h.streak + 1 } : h) }));
  };

  return (
    <div className="premium-dashboard">
      {/* ── Brief ── */}
      <section className="today-brief">
        <div className="brief-copy">
          <span>{new Date().toLocaleDateString("ru-RU", { weekday: "long", day: "numeric", month: "long" })}</span>
          <h2>Твой command center</h2>
          <p>Сегодня в фокусе сон, вода и открытые задачи</p>
        </div>
        <div className="brief-status">
          <StatusRow good={(metrics.latestHealth?.sleep ?? 0) >= (Number(data.settings.sleepGoal) || 7.5)} label="Сон" value={`${metrics.latestHealth?.sleep ?? 0}h / цель ${data.settings.sleepGoal || "—"}h`} />
          <StatusRow good={(metrics.latestHealth?.water ?? 0) >= (Number(data.settings.waterGoal) || 2.5)} label="Вода" value={`${metrics.latestHealth?.water ?? 0} / ${data.settings.waterGoal || "—"}L`} />
          <StatusRow good={metrics.activeTasks < 5} label="Открытые задачи" value={`${metrics.activeTasks}`} />
        </div>
        <div className="brief-kpis">
          {([
            ["Баланс", money.format(metrics.balance), CircleDollarSign],
            ["Расходы", money.format(metrics.expenses), Wallet],
            ["Калории", isAvg ? `~${metrics.calories}/д` : `${metrics.calories}`, Utensils],
            ["Сон", `${metrics.latestHealth?.sleep ?? 0}h`, Moon],
            ["Вода", `${metrics.latestHealth?.water ?? 0}L`, Activity],
            ["Задачи", `${metrics.activeTasks}`, CheckCircle2],
            ["Привычки", `${metrics.doneHabits}/${data.habits.length}`, Flame],
            ["Настроение", currentMood ? `${currentMood}/10` : "—", HeartPulse],
          ] as [string, string, typeof Activity][]).map(([label, value, Icon]) => (
            <div className="brief-kpi" key={label}>
              <Icon size={17} />
              <span>{label}</span>
              <strong>{value}</strong>
            </div>
          ))}
        </div>
      </section>

      {/* ── Main grid ── */}
      <div className="premium-grid">
        {/* Balance + mood + habits */}
        <section className="balance-panel">
          <div className="panel-topline">
            <span>Мой баланс</span>
            <button onClick={() => setPage("finance")}>Все финансы</button>
          </div>
          <div className="balance-amount">{money.format(metrics.balance)}</div>
          <div className="action-pills">
            <button onClick={() => setPage("finance")}><CircleDollarSign size={19} />Доход: {money.format(metrics.income)}</button>
            <button onClick={() => setPage("finance")}><Wallet size={19} />Расход: {money.format(metrics.expenses)}</button>
          </div>

          {/* Quick mood log */}
          <div style={{ display: "flex", alignItems: "baseline", justifyContent: "space-between", marginTop: 22, marginBottom: 10 }}>
            <h2>Настроение</h2>
            <button onClick={() => setPage("mood")} style={{ fontSize: 12, fontWeight: 650, color: "var(--accent)", background: "none", border: "none", cursor: "pointer" }}>
              Аналитика →
            </button>
          </div>
          {/* Status row */}
          {lastMoodEntry && (
            <div className="flex items-center gap-2.5 mb-3 px-3.5 py-2.5 rounded-2xl border border-[var(--border)] bg-[var(--glass-thin)] shadow-sm">
              <span style={{ fontSize: 24 }}>{MOOD_EMOJIS.find(([v]) => v === lastMoodEntry.value)?.[1]}</span>
              <div>
                <div style={{ fontSize: 13, fontWeight: 700 }}>{lastMoodEntry.label}</div>
                <div style={{ fontSize: 11, color: "var(--ink2)" }}>{fmtTime(lastMoodEntry.datetime)} · Сегодня {todayMoodEntries.length} запис{todayMoodEntries.length === 1 ? "ь" : "и"}</div>
              </div>
              <div style={{ marginLeft: "auto", position: "relative", width: 34, height: 34, display: "grid", placeItems: "center" }}>
                <svg width="34" height="34" style={{ transform: "rotate(-90deg)" }}>
                  <circle cx="17" cy="17" r={circleRadius} fill="none" stroke="var(--border)" strokeWidth="2.5" />
                  <circle cx="17" cy="17" r={circleRadius} fill="none" stroke={circleColor} strokeWidth="2.5"
                    strokeDasharray={circleCircumference} strokeDashoffset={strokeOffset} strokeLinecap="round"
                    style={{ transition: "stroke-dashoffset 0.3s ease" }} />
                </svg>
                <span className="absolute text-[10px] font-extrabold text-[var(--ink)]">{todayMoodAvg}</span>
              </div>
            </div>
          )}
          {/* Emoji buttons */}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {MOOD_EMOJIS.map(([val, emoji, label]) => (
              <button
                key={val}
                onClick={() => handleMoodLog(val)}
                title={label}
                style={{
                  display: "flex", flexDirection: "column", alignItems: "center", gap: 4,
                  width: 60, paddingTop: 10, paddingBottom: 8, borderRadius: 18,
                  border: `2px solid ${currentMood === val ? "var(--accent)" : "var(--border)"}`,
                  background: currentMood === val ? "var(--accent-glow)" : "var(--glass-thin)",
                  cursor: "pointer", transition: "all 0.15s", fontSize: 26, lineHeight: 1,
                  boxShadow: currentMood === val ? "0 4px 14px var(--accent-glow)" : "var(--specular)",
                }}
              >
                {emoji}
                <span style={{ fontSize: 10, fontWeight: 700, color: currentMood === val ? "var(--accent)" : "var(--ink2)" }}>{label}</span>
              </button>
            ))}
          </div>

          {/* Habits checklist */}
          <h2 style={{ marginTop: 22, marginBottom: 10 }}>Привычки сегодня</h2>
          <div style={{ display: "grid", gap: 8 }}>
            {data.habits.map((h) => {
              const done = h.doneDates.includes(iso(0));
              return (
                <button
                  key={h.id}
                  onClick={() => toggleHabit(h)}
                  style={{
                    display: "flex", alignItems: "center", gap: 10,
                    padding: "10px 14px", borderRadius: 16, cursor: "pointer", textAlign: "left",
                    border: `1px solid ${done ? "rgba(34,197,94,0.3)" : "var(--border)"}`,
                    background: done ? "rgba(34,197,94,0.12)" : "var(--glass-thin)",
                    fontSize: 13, fontWeight: 600, transition: "all 0.15s",
                  }}
                >
                  <span className={`check ${done ? "checked" : ""}`} style={{ flexShrink: 0 }} />
                  <span style={{ flex: 1 }}>{h.title}</span>
                  <span style={{ fontSize: 11, color: "var(--ink2)" }}>🔥 {h.streak}</span>
                </button>
              );
            })}
          </div>
        </section>

        {/* Spending bars */}
        <section className="spending-panel">
          <div className="panel-title-row">
            <h2>Расходы</h2>
            <button style={{ textTransform: "capitalize" }}>{periodLabel} <ChevronDown size={16} /></button>
          </div>
          <div className="bars-stage">
            {spendingBars.map((h, i) => (
              <div className="bar-item" key={finance[i].date}>
                <span className={i === 6 ? "hot" : ""} style={{ height: `${h}%` }} />
                <small>{finance[i].date}</small>
              </div>
            ))}
            <em>{money.format(metrics.expenses)}</em>
          </div>
        </section>

        {/* Insight */}
        <section className="manage-panel">
          <div>
            <h2>Что держит день?</h2>
            <p>
              {(metrics.latestHealth?.sleep ?? 0) >= (Number(data.settings.sleepGoal) || 7.5) ? "Сон в норме. " : "Сон недостаточный. "}
              {(metrics.latestHealth?.water ?? 0) >= (Number(data.settings.waterGoal) || 2.5) ? "Вода выполнена. " : "Вода отстаёт. "}
              {metrics.doneHabits >= Math.ceil(data.habits.length / 2) ? "Привычки идут хорошо." : "Привычки требуют внимания."}
            </p>
          </div>
          <div className="orbital-lines" />
          <button onClick={() => setPage("analytics")}>Аналитика</button>
        </section>

        {/* Tasks */}
        <section className="transactions-panel">
          <div className="panel-title-row">
            <h2>Задачи {periodLabel}</h2>
            <button onClick={() => setPage("tasks")}>Все задачи</button>
          </div>
          <div className="premium-list">
            {todayTasks.slice(0, 5).map((t) => (
              <button key={t.id} className="premium-list-row"
                onClick={() => setData((p) => ({ ...p, tasks: p.tasks.map((x) => x.id === t.id ? { ...x, status: "done" } : x) }))}>
                <span><CheckCircle2 size={19} /></span>
                <strong>{t.title}<small>{t.due}</small></strong>
                <em className={`priority ${t.priority}`}>{t.priority}</em>
              </button>
            ))}
            {todayTasks.length === 0 && (
              <p style={{ padding: "16px 0", color: "var(--ink2)", fontSize: 14 }}>Все задачи выполнены ✓</p>
            )}
          </div>
        </section>

        {/* Chart */}
        <section className="expenses-panel">
          <div className="panel-title-row">
            <h2>Тренд доходов (7 дней)</h2>
            <button>{new Date().toLocaleDateString("ru-RU", { day: "numeric", month: "short" })} <ChevronDown size={16} /></button>
          </div>
          <ChartWrap>
            <AreaChart data={finance.map((f, i) => ({ ...f, mood: (mood[i]?.mood ?? 6) * 750 }))}>
              <defs>
                <linearGradient id="premiumFill" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#8FB6FF" stopOpacity={0.68} />
                  <stop offset="100%" stopColor="#8FB6FF" stopOpacity={0.05} />
                </linearGradient>
              </defs>
               <CartesianGrid vertical={false} stroke="var(--grid-line)" />
              <XAxis dataKey="date" tickLine={false} axisLine={false} tick={{ fill: "var(--ink2)", fontSize: 11 }} />
              <YAxis hide />
              <Tooltip />
              <Area type="monotone" dataKey="income" stroke="#8FB6FF" fill="url(#premiumFill)" strokeWidth={4} />
              <Line type="monotone" dataKey="mood" stroke="var(--ink)" strokeWidth={3} dot={false} />
            </AreaChart>
          </ChartWrap>
        </section>

      </div>

      {/* ── Under grid ── */}
      <div className="under-grid">
        <Card>
          <SectionTitle title="Питание сегодня" sub={`${metrics.calories} / ${data.settings.caloriesGoal || "—"} kcal`} />
          <div className="space-y-2">
            {latestMeals.length > 0
              ? latestMeals.map((m) => <div key={m.id} className="list-row"><div><p className="font-medium">{m.name}</p><p className="text-sm text-slate-500">{m.mealType} · P{m.protein}/F{m.fat}/C{m.carbs}</p></div><span>{m.calories} kcal</span></div>)
              : <p className="text-sm text-slate-400 py-2">Нет записей. <button className="text-accent font-semibold" onClick={() => setPage("nutrition")}>Добавить</button></p>
            }
          </div>
        </Card>
        <Card>
          <SectionTitle title="Ближайшие события" />
          <div className="space-y-2">{data.events.slice(0, 4).map((e) => <button key={e.id} onClick={() => openEvent(e)} className="event-row"><span>{e.time}</span><strong>{e.title}</strong><small>{e.date}</small></button>)}</div>
        </Card>
        <Card>
          <SectionTitle title="Mood за 7 дней" />
          <ChartWrap small><LineChart data={mood}><XAxis dataKey="date" hide /><YAxis hide domain={[0, 10]} /><Tooltip /><Line type="monotone" dataKey="mood" stroke="var(--accent)" strokeWidth={3} dot={{ r: 3 }} /></LineChart></ChartWrap>
        </Card>
        <Card>
          <SectionTitle title="Цели" />
          <div className="space-y-4">
            {data.goals.slice(0, 3).map((g) => (
              <div key={g.id}>
                <div className="mb-2 flex justify-between text-sm"><span className="font-medium">{g.title}</span><span className="text-slate-500">{g.progress}%</span></div>
                <Progress value={g.progress} color={g.status === "behind" ? "bg-rose-400" : "bg-accent"} />
              </div>
            ))}
          </div>
        </Card>
      </div>
    </div>
  );
}

function StatusRow({ label, value, good = false }: { label: string; value: string; good?: boolean }) {
  return <div className="flex items-center justify-between rounded-xl bg-slate-50 p-3"><span className="text-sm text-slate-600">{label}</span><span className={`badge ${good ? "good" : "warn"}`}>{value}</span></div>;
}
function MiniStat({ label, value }: { label: string; value: string }) {
  return <div className="rounded-xl bg-slate-50 p-3 text-center"><p className="text-xs text-slate-400">{label}</p><p className="mt-1 font-semibold">{value}</p></div>;
}
function ChartWrap({ children, small = false }: { children: React.ReactElement; small?: boolean }) {
  return <div className={small ? "h-44 w-full" : "h-72 w-full"}><ResponsiveContainer width="100%" height="100%" minWidth={0}>{children}</ResponsiveContainer></div>;
}

// ─── Finance chart helpers ────────────────────────────────────────
type FP = "День" | "Неделя" | "Месяц" | "Год";
const FP_ALL: FP[] = ["День", "Неделя", "Месяц", "Год"];

function buildFinanceDataCombined(txs: Transaction[], period: FP) {
  const getVals = (list: Transaction[]) => {
    const income = list.filter(t => t.type === "income").reduce((s, t) => s + t.amount, 0);
    const expense = list.filter(t => t.type === "expense").reduce((s, t) => s + t.amount, 0);
    return { income, expense };
  };

  if (period === "День") {
    return Array.from({ length: 24 }, (_, h) => {
      const hour = txs.filter(t => t.date === iso(0) && parseInt(t.time?.slice(0, 2) ?? "0") === h);
      return { label: `${h}:00`, ...getVals(hour) };
    });
  }
  if (period === "Неделя") {
    return Array.from({ length: 7 }, (_, i) => {
      const date = iso(i - 6);
      return { label: date.slice(5), ...getVals(txs.filter(t => t.date === date)) };
    });
  }
  if (period === "Месяц") {
    return Array.from({ length: 30 }, (_, i) => {
      const date = iso(i - 29);
      return { label: date.slice(5), ...getVals(txs.filter(t => t.date === date)) };
    });
  }
  // Year — 12 months
  const now = new Date(today);
  const MONTHS_SHORT = ["Янв","Фев","Мар","Апр","Май","Июн","Июл","Авг","Сен","Окт","Ноя","Дек"];
  return Array.from({ length: 12 }, (_, i) => {
    const d = new Date(now.getFullYear(), now.getMonth() - 11 + i, 1);
    const prefix = `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
    return { label: MONTHS_SHORT[d.getMonth()], ...getVals(txs.filter(t => t.date.startsWith(prefix))) };
  });
}

function calcPeriodChange(txs: Transaction[], chartType: "income" | "expense" | "balance", period: FP): number {
  const getVal = (list: Transaction[]) => {
    const inc = list.filter(t => t.type === "income").reduce((s, t) => s + t.amount, 0);
    const exp = list.filter(t => t.type === "expense").reduce((s, t) => s + t.amount, 0);
    return chartType === "income" ? inc : chartType === "expense" ? exp : inc - exp;
  };
  let [curFilter, prevFilter]: [(t: Transaction) => boolean, (t: Transaction) => boolean] = [() => false, () => false];
  if (period === "День") {
    curFilter  = t => t.date === iso(0);
    prevFilter = t => t.date === iso(-1);
  } else if (period === "Неделя") {
    curFilter  = t => t.date >= iso(-6)  && t.date <= iso(0);
    prevFilter = t => t.date >= iso(-13) && t.date <= iso(-7);
  } else if (period === "Месяц") {
    curFilter  = t => t.date >= iso(-29);
    prevFilter = t => t.date >= iso(-59) && t.date <= iso(-30);
  } else {
    const thisY = iso(0).slice(0, 4);
    const prevY = String(Number(thisY) - 1);
    curFilter  = t => t.date.startsWith(thisY);
    prevFilter = t => t.date.startsWith(prevY);
  }
  const cur  = getVal(txs.filter(curFilter));
  const prev = getVal(txs.filter(prevFilter));
  if (!prev) return cur > 0 ? 100 : 0;
  return Math.round((cur - prev) / Math.abs(prev) * 100);
}

function FinanceCombinedTooltip({ active, payload, label, fmtC }: { active?: boolean; payload?: any[]; label?: string; fmtC: (n: number) => string }) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-xl border border-[var(--border)] bg-[var(--glass-heavy)] backdrop-blur-md px-3.5 py-2.5 shadow-xl min-w-[140px]">
      <div className="text-[10px] font-bold tracking-wider uppercase text-[var(--ink2)] mb-1.5">{label}</div>
      <div className="space-y-1">
        {payload.map((item) => (
          <div key={item.name} className="flex items-center justify-between gap-4">
            <span className="text-[11px] text-[var(--ink2)] font-semibold">{item.name}</span>
            <span className="text-sm font-extrabold" style={{ color: item.color }}>{fmtC(item.value)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function PremiumFinanceChart({ txs, fmtC }: {
  txs: Transaction[];
  fmtC: (n: number) => string;
}) {
  const [period, setPeriod] = useState<FP>("Неделя");
  const [displayMode, setDisplayMode] = useState<"all" | "income" | "expense">("all");

  const data = useMemo(() => buildFinanceDataCombined(txs, period), [txs, period]);

  const totals = useMemo(() => {
    let filter: (t: Transaction) => boolean;
    if (period === "День") {
      filter = t => t.date === iso(0);
    } else if (period === "Неделя") {
      filter = t => t.date >= iso(-6) && t.date <= iso(0);
    } else if (period === "Месяц") {
      filter = t => t.date >= iso(-29);
    } else {
      const thisY = iso(0).slice(0, 4);
      filter = t => t.date.startsWith(thisY);
    }
    const filtered = txs.filter(filter);
    const inc = filtered.filter(t => t.type === "income").reduce((s, t) => s + t.amount, 0);
    const exp = filtered.filter(t => t.type === "expense").reduce((s, t) => s + t.amount, 0);
    return { income: inc, expense: exp, balance: inc - exp };
  }, [txs, period]);

  const incomeChange = useMemo(() => calcPeriodChange(txs, "income", period), [txs, period]);
  const expenseChange = useMemo(() => calcPeriodChange(txs, "expense", period), [txs, period]);
  const balanceChange = useMemo(() => calcPeriodChange(txs, "balance", period), [txs, period]);

  return (
    <div className="finance-chart-card xl:col-span-3">
      {/* Top Header Row with Stats and Switchers */}
      <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-6 mb-6">
        {/* Dynamic Period Stats */}
        <div className="grid grid-cols-3 gap-6 md:gap-10">
          <div className="flex flex-col">
            <span className="text-[10px] font-bold tracking-wider uppercase text-slate-400">Доходы за период</span>
            <div className="flex items-baseline gap-1.5 mt-1">
              <span className="text-xl md:text-2xl font-extrabold text-[var(--text-income-raw)]">{fmtC(totals.income)}</span>
              <span className={`text-[11px] font-bold ${incomeChange >= 0 ? "text-cyan-500" : "text-rose-500"}`}>
                {incomeChange >= 0 ? "↑" : "↓"}{Math.abs(incomeChange)}%
              </span>
            </div>
          </div>
          <div className="flex flex-col">
            <span className="text-[10px] font-bold tracking-wider uppercase text-slate-400">Расходы за период</span>
            <div className="flex items-baseline gap-1.5 mt-1">
              <span className="text-xl md:text-2xl font-extrabold text-[var(--text-expense-raw)]">{fmtC(totals.expense)}</span>
              <span className={`text-[11px] font-bold ${expenseChange >= 0 ? "text-cyan-500" : "text-rose-500"}`}>
                {expenseChange >= 0 ? "↑" : "↓"}{Math.abs(expenseChange)}%
              </span>
            </div>
          </div>
          <div className="flex flex-col">
            <span className="text-[10px] font-bold tracking-wider uppercase text-slate-400">Чистый баланс</span>
            <div className="flex items-baseline gap-1.5 mt-1">
              <span className={`text-xl md:text-2xl font-extrabold ${totals.balance >= 0 ? "text-[var(--text-balance-raw)]" : "text-[var(--text-expense-raw)]"}`}>{fmtC(totals.balance)}</span>
              <span className={`text-[11px] font-bold ${balanceChange >= 0 ? "text-cyan-500" : "text-rose-500"}`}>
                {balanceChange >= 0 ? "↑" : "↓"}{Math.abs(balanceChange)}%
              </span>
            </div>
          </div>
        </div>

        {/* Action Controls */}
        <div className="flex flex-wrap items-center gap-3">
          {/* Display Switcher */}
          <div className="flex rounded-xl border border-[var(--border)] bg-[var(--glass-thin)] p-0.5">
            {([
              { key: "all", label: "Все вместе" },
              { key: "income", label: "Только доходы" },
              { key: "expense", label: "Только расходы" }
            ] as const).map((mode) => (
              <button key={mode.key} onClick={() => setDisplayMode(mode.key)}
                style={{
                  background: displayMode === mode.key ? "var(--accent)" : "transparent",
                  color: displayMode === mode.key ? "#fff" : "var(--ink2)",
                  boxShadow: displayMode === mode.key ? "var(--shadow-soft)" : "none",
                }}
                className="rounded-lg px-3 py-1.5 text-xs font-semibold transition">
                {mode.label}
              </button>
            ))}
          </div>

          {/* Period Tabs */}
          <div className="flex rounded-xl border border-[var(--border)] bg-[var(--glass-thin)] p-0.5">
            {FP_ALL.map((p) => (
              <button key={p} onClick={() => setPeriod(p)}
                style={{
                  background: period === p ? "var(--accent)" : "transparent",
                  color: period === p ? "#fff" : "var(--ink2)",
                  boxShadow: period === p ? "var(--shadow-soft)" : "none",
                }}
                className="rounded-lg px-3 py-1.5 text-xs font-semibold transition">
                {p}
              </button>
            ))}
          </div>
        </div>
      </div>

      {/* Chart Canvas */}
      <div style={{ height: 260, width: "100%" }}>
        <ResponsiveContainer width="100%" height="100%" minWidth={0}>
          <AreaChart data={data} margin={{ top: 8, right: 4, bottom: 0, left: 0 }}>
            <defs>
              <linearGradient id="grad-combined-income" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--text-income-raw)" stopOpacity={0.25} />
                <stop offset="100%" stopColor="var(--text-income-raw)" stopOpacity={0.02} />
              </linearGradient>
              <linearGradient id="grad-combined-expense" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="var(--text-expense-raw)" stopOpacity={0.25} />
                <stop offset="100%" stopColor="var(--text-expense-raw)" stopOpacity={0.02} />
              </linearGradient>
            </defs>
            <CartesianGrid vertical={false} stroke="var(--grid-line)" strokeDasharray="0" />
            <XAxis
              dataKey="label"
              tickLine={false}
              axisLine={false}
              tick={{ fontSize: 11, fill: "var(--ink2)", fontWeight: 600 }}
              interval={period === "Месяц" ? 4 : period === "День" ? 3 : 0}
            />
            <YAxis hide domain={["auto", "auto"]} />
            <Tooltip content={(props: any) => <FinanceCombinedTooltip {...props} fmtC={fmtC} />} />
            
            {(displayMode === "all" || displayMode === "income") && (
              <Area
                type="monotone"
                dataKey="income"
                name="Доходы"
                stroke="var(--text-income-raw)"
                strokeWidth={2.5}
                fill="url(#grad-combined-income)"
                dot={false}
                activeDot={{ r: 5, fill: "var(--text-income-raw)", stroke: "white", strokeWidth: 2.5 }}
              />
            )}
            {(displayMode === "all" || displayMode === "expense") && (
              <Area
                type="monotone"
                dataKey="expense"
                name="Расходы"
                stroke="var(--text-expense-raw)"
                strokeWidth={2.5}
                fill="url(#grad-combined-expense)"
                dot={false}
                activeDot={{ r: 5, fill: "var(--text-expense-raw)", stroke: "white", strokeWidth: 2.5 }}
              />
            )}
          </AreaChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}

function Finance({ data, add, setData }: { data: AppData; add: any; setData: React.Dispatch<React.SetStateAction<AppData>> }) {
  const [currency, setCurrency] = useState<Currency>("UAH");
  const [uahRate, setUahRate] = useState(41.5); // fallback rate UAH per 1 USD
  const [rateLoading, setRateLoading] = useState(false);
  const [addMode, setAddMode] = useState<"expense" | "income" | null>(null);
  const [newCat, setNewCat] = useState("");
  const [showCatEditor, setShowCatEditor] = useState<"expense" | "income" | null>(null);

  useEffect(() => {
    setRateLoading(true);
    fetch("https://api.exchangerate-api.com/v4/latest/USD")
      .then((r) => r.json())
      .then((d) => { if (d?.rates?.UAH) setUahRate(d.rates.UAH); })
      .catch(() => {})
      .finally(() => setRateLoading(false));
  }, []);

  const conv = (uah: number) => currency === "UAH" ? uah : uah / uahRate;
  const fmtC = (uah: number) => new Intl.NumberFormat("uk-UA", {
    style: "currency", currency: currency === "UAH" ? "UAH" : "USD",
    maximumFractionDigits: currency === "UAH" ? 0 : 1,
  }).format(conv(uah));

  const expCats = Object.values(
    data.transactions.filter((t) => t.type === "expense")
      .reduce<Record<string, { name: string; value: number }>>((a, t) => ({
        ...a, [t.category]: { name: t.category, value: (a[t.category]?.value ?? 0) + t.amount }
      }), {})
  );

  const addCat = (type: "expense" | "income") => {
    if (!newCat.trim()) return;
    const key = type === "expense" ? "expenseCategories" : "incomeCategories";
    setData((p) => ({ ...p, [key]: [...p[key], newCat.trim()] }));
    setNewCat("");
  };
  const removeCat = (type: "expense" | "income", cat: string) => {
    const key = type === "expense" ? "expenseCategories" : "incomeCategories";
    setData((p) => ({ ...p, [key]: p[key].filter((c) => c !== cat) }));
  };

  return (
    <PageGrid>
      {/* Currency switcher */}
      <div className="card xl:col-span-3 flex items-center justify-between gap-4 !py-3 !px-5 shadow-soft">
        <div className="flex items-center gap-2 text-sm font-medium text-slate-500">
          {rateLoading ? "Загружаем курс…" : `Курс: 1 USD = ${uahRate.toFixed(1)} UAH`}
        </div>
        <div className="flex rounded-full border border-[var(--border)] bg-[var(--glass-thin)] p-1">
          {(["UAH", "USD"] as Currency[]).map((c) => (
            <button key={c} onClick={() => setCurrency(c)}
              style={{ color: currency === c ? "" : "var(--ink2)" }}
              className={`rounded-full px-4 py-1.5 text-sm font-semibold transition ${currency === c ? "bg-accent text-white shadow-soft" : ""}`}>
              {c === "UAH" ? "₴ Гривны" : "$ Доллары"}
            </button>
          ))}
        </div>
        <div className="flex gap-2">
          <button className="primary-btn" onClick={() => setAddMode("expense")}><Plus size={16} />Расход</button>
          <button className="primary-btn" style={{ background: "linear-gradient(135deg,#0ea5e9,#0284c7)" }} onClick={() => setAddMode("income")}><Plus size={16} />Доход</button>
        </div>
      </div>

      {/* ── Premium charts ── */}
      <PremiumFinanceChart txs={data.transactions} fmtC={fmtC} />

      {/* ── Category donut ── */}
      <Card>
        <SectionTitle title="Расходы по категориям" />
        <ChartWrap>
          <PieChart>
            <Pie data={expCats} dataKey="value" nameKey="name" innerRadius={55} outerRadius={92}>
              {expCats.map((_, i) => <Cell key={i} fill={["#F47C20","#22A06B","#3B82F6","#A855F7","#F59E0B","#EF4444","#06B6D4"][i % 7]} />)}
            </Pie>
            <Tooltip formatter={(v: unknown) => fmtC(Number(v))} />
          </PieChart>
        </ChartWrap>
      </Card>

      {/* Category editors */}
      <Card>
        <SectionTitle title="Категории расходов" sub="редактировать" />
        <div className="flex flex-wrap gap-2 mb-3">
          {data.expenseCategories.map((c) => (
            <span key={c} className="expense-tag">
              {c}
              <button onClick={() => removeCat("expense", c)} className="ml-1 opacity-60 hover:opacity-100 hover:text-rose-500">×</button>
            </span>
          ))}
        </div>
        <div className="flex gap-2">
          <input value={newCat} onChange={(e) => setNewCat(e.target.value)} onKeyDown={(e) => e.key === "Enter" && (addCat("expense"), setShowCatEditor(null))}
            placeholder="Новая категория" className="flex-1 rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-accent" />
          <button className="primary-btn" onClick={() => addCat("expense")}><Plus size={15} /></button>
        </div>
      </Card>

      <Card>
        <SectionTitle title="Категории доходов" sub="редактировать" />
        <div className="flex flex-wrap gap-2 mb-3">
          {data.incomeCategories.map((c) => (
            <span key={c} className="income-tag">
              {c}
              <button onClick={() => removeCat("income", c)} className="ml-1 opacity-60 hover:opacity-100 hover:text-rose-500">×</button>
            </span>
          ))}
        </div>
        <div className="flex gap-2">
          <input value={newCat} onChange={(e) => setNewCat(e.target.value)} onKeyDown={(e) => e.key === "Enter" && addCat("income")}
            placeholder="Новая категория" className="flex-1 rounded-xl border border-slate-200 px-3 py-2 text-sm outline-none focus:border-accent" />
          <button className="primary-btn" style={{ background: "linear-gradient(135deg,#0ea5e9,#0284c7)" }} onClick={() => addCat("income")}><Plus size={15} /></button>
        </div>
      </Card>

      <Card className="xl:col-span-3">
        <SectionTitle title="Все транзакции" sub={`${data.transactions.length} записей`} />
        <TransactionsTable
          rows={data.transactions}
          fmtC={fmtC}
          setData={setData}
          expenseCategories={data.expenseCategories}
          incomeCategories={data.incomeCategories}
        />
      </Card>

      {/* Modals */}
      {addMode === "expense" && (
        <Modal title="Добавить расход" close={() => setAddMode(null)}>
          <ExpenseForm add={add} categories={data.expenseCategories} after={() => setAddMode(null)} />
        </Modal>
      )}
      {addMode === "income" && (
        <Modal title="Добавить доход" close={() => setAddMode(null)}>
          <IncomeForm add={add} categories={data.incomeCategories} after={() => setAddMode(null)} />
        </Modal>
      )}
    </PageGrid>
  );
}

function Nutrition({ data, add, setData }: { data: AppData; add: any; setData: React.Dispatch<React.SetStateAction<AppData>> }) {
  const m = getMetrics(data);
  const chart = chart7(data.meals, (xs) => ({ calories: xs.reduce((s, x) => s + x.calories, 0) }));
  const [addFood, setAddFood] = useState(false);
  const [addMeal, setAddMeal] = useState(false);
  const [editGoals, setEditGoals] = useState(false);
  const [editingFood, setEditingFood] = useState<FoodItem | null>(null);
  const [editingMeal, setEditingMeal] = useState<any | null>(null);

  const handleDeleteFood = (foodId: string) => {
    setData((p) => ({
      ...p,
      foods: p.foods.filter((f) => f.id !== foodId),
    }));
    deleteProductFromSupabase(foodId);
  };

  const handleDeleteMeal = (mealId: string) => {
    setData((p) => ({
      ...p,
      meals: p.meals.filter((mm) => mm.id !== mealId),
    }));
    deleteMealFromSupabase(mealId);
  };

  const pGoal = Number(data.settings.proteinGoal) || 150;
  const fGoal = Number(data.settings.fatGoal) || 70;
  const cGoal = Number(data.settings.carbsGoal) || 250;
  const getPct = (val: number, goal: number) => goal > 0 ? Math.round((val / goal) * 100) : 0;

  return (
    <PageGrid>
      <Kpi
        title="Калории сегодня"
        value={`${m.calories} / ${data.settings.caloriesGoal || "—"} ккал`}
        sub={`осталось ${Math.max((Number(data.settings.caloriesGoal) || 2200) - m.calories, 0)} ккал · ${getPct(m.calories, Number(data.settings.caloriesGoal) || 2200)}%`}
        icon={Utensils}
      />
      <Kpi
        title="Белки сегодня"
        value={`${m.macros.protein} / ${data.settings.proteinGoal || "—"} г`}
        sub={`осталось ${Math.max(pGoal - m.macros.protein, 0)} г · ${getPct(m.macros.protein, pGoal)}%`}
        icon={Activity}
        tone="green"
      />
      <Kpi
        title="Жиры сегодня"
        value={`${m.macros.fat} / ${data.settings.fatGoal || "—"} г`}
        sub={`осталось ${Math.max(fGoal - m.macros.fat, 0)} г · ${getPct(m.macros.fat, fGoal)}%`}
        icon={Activity}
        tone="blue"
      />
      <Kpi
        title="Углеводы сегодня"
        value={`${m.macros.carbs} / ${data.settings.carbsGoal || "—"} г`}
        sub={`осталось ${Math.max(cGoal - m.macros.carbs, 0)} г · ${getPct(m.macros.carbs, cGoal)}%`}
        icon={Activity}
        tone="red"
      />

      {/* Action buttons */}
      <div className="xl:col-span-3 flex gap-3">
        <button className="primary-btn" onClick={() => setAddMeal(true)}><Plus size={16} />Добавить приём пищи</button>
        <button className="primary-btn" style={{ background: "linear-gradient(135deg,#3B82F6,#2563EB)" }} onClick={() => setAddFood(true)}><Plus size={16} />Добавить продукт в базу</button>
      </div>

      {/* Daily progress */}
      <Card>
        <SectionTitle
          title="Прогресс дня"
          sub={
            <button
              onClick={() => setEditGoals(true)}
              className="text-xs font-bold text-accent hover:underline flex items-center gap-1"
            >
              ✏️ Изменить цели
            </button>
          }
        />
        <div className="space-y-4">
          <div className="space-y-1">
            <div className="flex justify-between text-sm">
              <span className="font-semibold text-sm">Калории</span>
              <span className="font-bold text-accent">{getPct(m.calories, Number(data.settings.caloriesGoal) || 2200)}%</span>
            </div>
            <Progress value={getPct(m.calories, Number(data.settings.caloriesGoal) || 2200)} />
            <div className="flex justify-between text-xs text-slate-500 font-medium">
              <span>Съедено: {m.calories} ккал</span>
              <span>Цель: {data.settings.caloriesGoal || "—"} ккал</span>
              <span>Осталось: {Math.max((Number(data.settings.caloriesGoal) || 2200) - m.calories, 0)} ккал</span>
            </div>
          </div>
          {[
            ["Белки", m.macros.protein, pGoal, "bg-emerald-400"],
            ["Жиры", m.macros.fat, fGoal, "bg-blue-400"],
            ["Углеводы", m.macros.carbs, cGoal, "bg-orange-400"]
          ].map(([label, val, goal, color]) => {
            const valNum = val as number;
            const goalNum = goal as number;
            const pct = getPct(valNum, goalNum);
            const remaining = Math.max(goalNum - valNum, 0);
            return (
              <div key={label as string} className="space-y-1 pt-1">
                <div className="flex justify-between text-sm">
                  <span className="font-semibold text-sm">{label as string}</span>
                  <span className="font-bold text-slate-700 dark:text-slate-200">{pct}%</span>
                </div>
                <Progress value={pct} color={color as string} />
                <div className="flex justify-between text-xs text-slate-500 font-medium">
                  <span>Съедено: {valNum} г</span>
                  <span>Цель: {goalNum} г</span>
                  <span>Осталось: {remaining} г</span>
                </div>
              </div>
            );
          })}
        </div>
      </Card>

      <Card className="xl:col-span-2">
        <SectionTitle title="Калории за 7 дней" />
        <ChartWrap>
          <BarChart data={chart}>
            <CartesianGrid vertical={false} stroke="var(--grid-line)" />
            <XAxis dataKey="date" tickLine={false} axisLine={false} tick={{ fill: "var(--ink2)", fontSize: 11 }} />
            <YAxis hide />
            <Tooltip />
            <Bar dataKey="calories" fill="var(--accent)" radius={[8, 8, 0, 0]} />
          </BarChart>
        </ChartWrap>
      </Card>

      {/* Food database */}
      <Card className="xl:col-span-3">
        <SectionTitle title="База продуктов" sub={`${data.foods.length} продуктов`} />
        <div className="overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>Продукт</th>
                <th>Ккал/100г</th>
                <th>Белки</th>
                <th>Жиры</th>
                <th>Углеводы</th>
                <th>Действия</th>
              </tr>
            </thead>
            <tbody>
              {data.foods.map((f) => (
                <tr key={f.id}>
                  <td className="font-medium">{f.name}</td>
                  <td>{f.cal100}</td>
                  <td>{f.pro100} г</td>
                  <td>{f.fat100} г</td>
                  <td>{f.carb100} г</td>
                  <td>
                    <div className="flex gap-1.5">
                      <button
                        onClick={() => setEditingFood(f)}
                        className="rounded-lg px-2 py-1 text-xs font-semibold text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800 transition"
                        title="Редактировать"
                      >
                        ✏️
                      </button>
                      <button
                        onClick={() => handleDeleteFood(f.id)}
                        className="rounded-lg px-2 py-1 text-xs font-semibold text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/30 transition"
                        title="Удалить"
                      >
                        🗑
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {/* Meals log */}
      <Card className="xl:col-span-3">
        <SectionTitle title="Дневник питания" />
        <div className="overflow-x-auto">
          <table className="table">
            <thead><tr><th>Дата</th><th>Продукт</th><th>Приём</th><th>Вес</th><th>Ккал</th><th>Б</th><th>Ж</th><th>У</th><th></th></tr></thead>
            <tbody>
              {[...data.meals].sort((a, b) => b.date.localeCompare(a.date)).map((m) => (
                <tr key={m.id}>
                  <td>{m.date}</td>
                  <td className="font-medium">{m.name}</td>
                  <td><span className="rounded-full bg-orange-50 px-2 py-0.5 text-xs font-semibold text-accent">{m.mealType}</span></td>
                  <td>{m.weight} {(m as any).unit ?? "г"}</td>
                  <td className="font-semibold">{m.calories}</td>
                  <td>{m.protein}</td>
                  <td>{m.fat}</td>
                  <td>{m.carbs}</td>
                  <td>
                    <div className="flex gap-1.5">
                      <button
                        onClick={() => setEditingMeal(m)}
                        className="rounded-lg px-2 py-1 text-xs font-semibold text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800 transition"
                        title="Редактировать"
                      >
                        ✏️
                      </button>
                      <button
                        onClick={() => handleDeleteMeal(m.id)}
                        className="rounded-lg px-2 py-1 text-xs font-semibold text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/30 transition"
                        title="Удалить запись"
                      >
                        🗑
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {addMeal && (
        <Modal title="Добавить приём пищи" close={() => setAddMeal(false)}>
          <MealFormSmart foods={data.foods} add={add} after={() => setAddMeal(false)} />
        </Modal>
      )}
      {addFood && (
        <Modal title="Добавить продукт в базу" close={() => setAddFood(false)}>
          <FoodForm setData={setData} after={() => setAddFood(false)} />
        </Modal>
      )}
      {editGoals && (
        <Modal title="Редактировать дневные цели" close={() => setEditGoals(false)}>
          <NutritionGoalsForm settings={data.settings} setData={setData} after={() => setEditGoals(false)} />
        </Modal>
      )}
      {editingFood && (
        <Modal title="Редактировать продукт" close={() => setEditingFood(null)}>
          <FoodEditForm food={editingFood} setData={setData} after={() => setEditingFood(null)} />
        </Modal>
      )}
      {editingMeal && (
        <Modal title="Редактировать запись" close={() => setEditingMeal(null)}>
          <MealEditForm meal={editingMeal} setData={setData} after={() => setEditingMeal(null)} />
        </Modal>
      )}
    </PageGrid>
  );
}

const BODY_FIELDS: { key: keyof BodyLog; label: string; unit: string; color: string }[] = [
  { key: "weight",     label: "Вес",           unit: "кг",   color: "#3B82F6" },
  { key: "bmi",        label: "ИМТ",           unit: "",     color: "#8B5CF6" },
  { key: "fatPct",     label: "Жир",           unit: "%",    color: "#EF4444" },
  { key: "musclePct",  label: "Мышцы",         unit: "%",    color: "#22A06B" },
  { key: "waterPct",   label: "Вода",          unit: "%",    color: "#0EA5E9" },
  { key: "boneMass",   label: "Кости",         unit: "кг",   color: "#F59E0B" },
  { key: "metabolism", label: "Метаболизм",    unit: "ккал", color: "#F47C20" },
  { key: "proteinPct", label: "Белок",         unit: "%",    color: "#10B981" },
  { key: "bodyAge",    label: "Возраст тела",  unit: "",     color: "#6366F1" },
  { key: "visceralFat",label: "Висц. жир",     unit: "",     color: "#DC2626" },
  { key: "fatKg",      label: "Жир кг",        unit: "кг",   color: "#FB7185" },
  { key: "leanMass",   label: "Без жира",      unit: "кг",   color: "#34D399" },
  { key: "muscleKg",   label: "Мышцы кг",      unit: "кг",   color: "#4ADE80" },
  { key: "proteinKg",  label: "Протеин",       unit: "кг",   color: "#A78BFA" },
];

function Health({ data, add, setData }: { data: AppData; add: any; setData: React.Dispatch<React.SetStateAction<AppData>> }) {
  const latest = getMetrics(data).latestHealth;
  const latestBody = [...(data.bodyLogs ?? [])].sort((a, b) => b.date.localeCompare(a.date))[0];
  const [addBody, setAddBody] = useState(false);
  const [addHealth, setAddHealth] = useState(false);
  const weightChart = (data.bodyLogs ?? []).slice(-14).map((b) => ({ date: b.date.slice(5), weight: b.weight, fat: b.fatPct, muscle: b.musclePct }));

  return (
    <PageGrid>
      <Kpi title="Сон" value={`${latest?.sleep ?? 0} ч`} sub="последняя ночь" icon={Moon} tone="blue" />
      <Kpi title="Вода" value={`${latest?.water ?? 0} L`} sub="сегодня" icon={Activity} />
      <Kpi title="Вес" value={latestBody ? `${latestBody.weight} кг` : "—"} sub="последнее взвешивание" icon={Activity} tone="green" />

      {/* Body metrics grid */}
      {latestBody && (
        <Card className="xl:col-span-3">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="text-base font-semibold">Состав тела</h2>
              <p className="text-sm text-slate-500">Последнее взвешивание: {latestBody.date}</p>
            </div>
            <button className="primary-btn" onClick={() => setAddBody(true)}><Plus size={16} />Добавить замер</button>
          </div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-7">
            {BODY_FIELDS.map(({ key, label, unit, color }) => (
              <div key={key} style={{ borderLeft: `3px solid ${color}` }}
                className="rounded-xl border border-[var(--border)] bg-[var(--glass-thin)] px-3 py-3 shadow-line">
                <div className="text-[11px] font-bold uppercase tracking-wide text-[var(--ink2)]">{label}</div>
                <div className="mt-1 text-xl font-bold" style={{ color }}>{latestBody[key]}{unit}</div>
              </div>
            ))}
          </div>
        </Card>
      )}

      {/* Charts */}
      {weightChart.length > 0 && (
        <>
          <Card className="xl:col-span-2">
            <SectionTitle title="Вес (кг)" />
            <ChartWrap small>
              <LineChart data={weightChart}>
                <CartesianGrid vertical={false} stroke="var(--grid-line)" />
                <XAxis dataKey="date" tickLine={false} axisLine={false} tick={{ fill: "var(--ink2)", fontSize: 11 }} />
                <YAxis hide domain={["auto", "auto"]} />
                <Tooltip />
                <Line dataKey="weight" stroke="var(--accent)" strokeWidth={3} dot={{ r: 4, fill: "var(--accent)" }} />
              </LineChart>
            </ChartWrap>
          </Card>
          <Card>
            <SectionTitle title="Жир % vs Мышцы %" />
            <ChartWrap small>
              <LineChart data={weightChart}>
                <CartesianGrid vertical={false} stroke="var(--grid-line)" />
                <XAxis dataKey="date" tickLine={false} axisLine={false} tick={{ fill: "var(--ink2)", fontSize: 11 }} />
                <YAxis hide />
                <Tooltip />
                <Line dataKey="fat" stroke="#EF4444" strokeWidth={2} dot={false} />
                <Line dataKey="muscle" stroke="#22A06B" strokeWidth={2} dot={false} />
              </LineChart>
            </ChartWrap>
          </Card>
        </>
      )}

      {/* Buttons */}
      <div className="xl:col-span-3 flex gap-3">
        <button className="primary-btn" onClick={() => setAddBody(true)}><Plus size={16} />Добавить замер тела</button>
        <button className="primary-btn" style={{ background: "linear-gradient(135deg,#0EA5E9,#0284C7)" }} onClick={() => setAddHealth(true)}><Plus size={16} />Сон / Вода / Настроение</button>
      </div>

      {/* Body logs history */}
      <Card className="xl:col-span-3">
        <SectionTitle title="История замеров" />
        <div className="overflow-x-auto">
          <table className="table">
            <thead>
              <tr>
                <th>Дата</th>
                {BODY_FIELDS.slice(0, 8).map((f) => <th key={f.key}>{f.label}</th>)}
              </tr>
            </thead>
            <tbody>
              {[...(data.bodyLogs ?? [])].sort((a, b) => b.date.localeCompare(a.date)).map((b) => (
                <tr key={b.id}>
                  <td>{b.date}</td>
                  {BODY_FIELDS.slice(0, 8).map((f) => <td key={f.key}>{b[f.key]}{f.unit}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>

      {addBody && (
        <Modal title="Добавить замер тела" close={() => setAddBody(false)}>
          <BodyLogForm setData={setData} after={() => setAddBody(false)} />
        </Modal>
      )}
      {addHealth && (
        <Modal title="Сон / Вода / Настроение" close={() => setAddHealth(false)}>
          <HealthForm add={add} after={() => setAddHealth(false)} />
        </Modal>
      )}
    </PageGrid>
  );
}

function Workouts({ data, add }: { data: AppData; add: any }) {
  const weekly = chart7(data.workouts, (xs) => ({ calories: xs.reduce((s, x) => s + x.calories, 0), duration: xs.reduce((s, x) => s + x.duration, 0), steps: xs.reduce((s, x) => s + x.steps, 0) / 1000 }));
  const total = data.workouts.reduce((a, w) => ({ calories: a.calories + w.calories, duration: a.duration + w.duration, steps: a.steps + w.steps }), { calories: 0, duration: 0, steps: 0 });
  return <PageGrid>
    <Kpi title="Calories burned" value={`${total.calories}`} sub="recent workouts" icon={Flame} />
    <Kpi title="Steps" value={`${Math.round(total.steps / 1000)}k`} sub="logged activity" icon={Activity} tone="green" />
    <Kpi title="Duration" value={`${total.duration}m`} sub="training time" icon={Dumbbell} tone="blue" />
    <Kpi title="Sessions" value={`${data.workouts.length}`} sub="this period" icon={CheckCircle2} />
    <Card><SectionTitle title="Добавить тренировку" /><WorkoutForm add={add} /></Card>
    <Card className="xl:col-span-2"><SectionTitle title="Weekly activity" /><ChartWrap><BarChart data={weekly}><CartesianGrid vertical={false} stroke="var(--grid-line)" /><XAxis dataKey="date" tickLine={false} axisLine={false} tick={{ fill: "var(--ink2)", fontSize: 11 }} /><YAxis hide /><Tooltip /><Bar dataKey="duration" fill="var(--accent)" radius={[7, 7, 0, 0]} /><Bar dataKey="steps" fill="#007AFF" radius={[7, 7, 0, 0]} /></BarChart></ChartWrap></Card>
    <Card className="xl:col-span-3"><SectionTitle title="Workout list" /><SimpleList items={data.workouts.map((w) => [w.type, `${w.duration} min · ${w.calories} kcal · ${w.steps} steps`, w.date])} /></Card>
  </PageGrid>;
}

function Tasks({ data, setData, add }: { data: AppData; setData: React.Dispatch<React.SetStateAction<AppData>>; add: any }) {
  return <PageGrid>
    <Card><SectionTitle title="Добавить задачу" /><TaskForm add={add} /></Card>
    {(["today", "upcoming", "done"] as TaskStatus[]).map((status) => (
      <Card key={status}>
        <SectionTitle title={status === "today" ? "Today" : status === "upcoming" ? "Upcoming" : "Done"} />
        <TaskList tasks={data.tasks.filter((t) => t.status === status)} setData={setData} />
      </Card>
    ))}
  </PageGrid>;
}

function Habits({ data, setData, add }: { data: AppData; setData: React.Dispatch<React.SetStateAction<AppData>>; add: any }) {
  return <PageGrid>
    <Card><SectionTitle title="Добавить привычку" /><HabitForm add={add} /></Card>
    <Card className="xl:col-span-2"><SectionTitle title="Daily check-in grid" /><div className="space-y-4">{data.habits.map((h) => <HabitLine key={h.id} habit={h} setData={setData} grid />)}</div></Card>
    <Card className="xl:col-span-3"><SectionTitle title="Streaks + completion" /><div className="space-y-3">{data.habits.map((h) => <HabitLine key={h.id} habit={h} setData={setData} />)}</div></Card>
  </PageGrid>;
}

function Goals({ data, add }: { data: AppData; add: any }) {
  return <PageGrid>
    <Card><SectionTitle title="Добавить цель" /><GoalForm add={add} /></Card>
    {data.goals.map((g) => (
      <Card key={g.id}>
        <div className="mb-2 flex justify-between items-center">
          <span className="font-semibold text-sm">{g.title}</span>
          <span className={`badge ${g.status === 'completed' ? 'good' : g.status === 'behind' ? 'warn' : ''}`}>{g.status}</span>
        </div>
        <Progress value={g.progress} color={g.status === 'behind' ? 'bg-rose-400' : 'bg-accent'} />
        <div className="mt-2 flex justify-between text-xs text-slate-400">
          <span>{g.progress}% выполнено</span>
          <span>До {g.targetDate}</span>
        </div>
        <div className="mt-1 text-xs text-slate-400">Связано: {g.linked}</div>
      </Card>
    ))}
  </PageGrid>;
}

function Journal({ data, add }: { data: AppData; add: any }) {
  return <PageGrid>
    <Card><SectionTitle title="Новая запись" /><JournalForm add={add} /></Card>
    <Card className="xl:col-span-2">
      <SectionTitle title="Дневник" />
      <div className="space-y-4">
        {data.journal.slice().sort((a, b) => b.date.localeCompare(a.date)).map((e) => (
          <div key={e.id} className="rounded-xl border border-[var(--border-thin)] p-4 bg-[var(--glass)]">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs text-slate-400">{e.date}</span>
              <span className="badge good">{e.mood}</span>
            </div>
            <p className="text-sm leading-relaxed">{e.text}</p>
          </div>
        ))}
        {data.journal.length === 0 && <p className="text-sm text-slate-400">Записей пока нет</p>}
      </div>
    </Card>
  </PageGrid>;
}

function CalendarPage({ data, add, openEvent }: { data: AppData; add: any; openEvent: (e: EventItem) => void }) {
  const today = iso(0);
  const [viewDate, setViewDate] = useState(new Date());
  const [selectedDay, setSelectedDay] = useState<string | null>(null);

  const year = viewDate.getFullYear();
  const month = viewDate.getMonth();

  const firstDay = new Date(year, month, 1);
  const lastDay = new Date(year, month + 1, 0);
  const startOffset = (firstDay.getDay() + 6) % 7; // Mon=0
  const daysInMonth = lastDay.getDate();

  const monthName = viewDate.toLocaleDateString('ru-RU', { month: 'long', year: 'numeric' });

  const isoDate = (y: number, m: number, d: number) => {
    const mm = String(m + 1).padStart(2, '0');
    const dd = String(d).padStart(2, '0');
    return `${y}-${mm}-${dd}`;
  };

  const eventsForDay = (dateStr: string) => data.events.filter(e => e.date === dateStr);

  const cells: (string | null)[] = [
    ...Array(startOffset).fill(null),
    ...Array.from({ length: daysInMonth }, (_, i) => isoDate(year, month, i + 1)),
  ];

  const prevMonth = () => setViewDate(d => new Date(d.getFullYear(), d.getMonth() - 1, 1));
  const nextMonth = () => setViewDate(d => new Date(d.getFullYear(), d.getMonth() + 1, 1));

  const selectedEvents = selectedDay ? eventsForDay(selectedDay) : [];

  const TYPE_COLORS: Record<string, string> = {
    Work: '#6366f1', Fitness: '#10b981', Personal: '#f59e0b', Finance: '#3b82f6',
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between bg-[var(--glass)] border border-[var(--border)] rounded-2xl px-5 py-3.5 shadow-xl">
        <div>
          <h2 className="text-xl font-extrabold capitalize">{monthName}</h2>
          <p className="text-xs text-slate-400 mt-0.5">Нажмите на день для просмотра событий</p>
        </div>
        <div className="flex gap-2">
          <button className="icon-btn" onClick={prevMonth} aria-label="Предыдущий месяц">&#8249;</button>
          <button className="icon-btn" onClick={() => setViewDate(new Date())} aria-label="Сегодня" title="Сегодня">●</button>
          <button className="icon-btn" onClick={nextMonth} aria-label="Следующий месяц">&#8250;</button>
        </div>
      </div>

      <Card>
        {/* Weekday headers */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: '6px', marginBottom: '6px' }}>
          {['Пн','Вт','Ср','Чт','Пт','Сб','Вс'].map(d => (
            <div key={d} style={{ textAlign: 'center', fontSize: '11px', fontWeight: 600, color: 'var(--ink2)', padding: '4px 0' }}>{d}</div>
          ))}
        </div>
        {/* Calendar grid */}
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: '6px' }}>
          {cells.map((dateStr, i) => {
            if (!dateStr) return <div key={`empty-${i}`} style={{ minHeight: '88px' }} />;
            const dayEvents = eventsForDay(dateStr);
            const isToday = dateStr === today;
            const isSelected = dateStr === selectedDay;
            const dayNum = parseInt(dateStr.slice(8));
            return (
              <button
                key={dateStr}
                onClick={() => setSelectedDay(dateStr === selectedDay ? null : dateStr)}
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  alignItems: 'flex-start',
                  minHeight: '88px',
                  width: '100%',
                  padding: '8px',
                  borderRadius: 'var(--r-md)',
                  border: isSelected ? '2px solid var(--accent)' : isToday ? '2px solid var(--accent)' : '1px solid var(--border-thin)',
                  background: isSelected ? 'rgba(244,124,32,0.1)' : isToday ? 'rgba(244,124,32,0.07)' : 'rgba(255,255,255,0.36)',
                  cursor: 'pointer',
                  textAlign: 'left',
                  transition: 'all 0.14s ease',
                  overflow: 'hidden',
                  boxSizing: 'border-box',
                }}
                aria-label={`${dateStr}${dayEvents.length ? ', ' + dayEvents.length + ' событий' : ''}`}
              >
                <span style={{
                  fontSize: '13px',
                  fontWeight: isToday ? 700 : 600,
                  color: isToday ? 'var(--accent)' : 'inherit',
                  flexShrink: 0,
                }}>{dayNum}</span>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '3px', width: '100%', overflow: 'hidden', flex: 1, marginTop: dayEvents.length ? '4px' : 0 }}>
                  {dayEvents.slice(0, 2).map(ev => (
                    <span key={ev.id} style={{
                      display: 'block',
                      width: '100%',
                      overflow: 'hidden',
                      textOverflow: 'ellipsis',
                      whiteSpace: 'nowrap',
                      padding: '2px 5px',
                      borderRadius: '4px',
                      background: 'rgba(244,124,32,0.15)',
                      fontSize: '10px',
                      fontWeight: 600,
                      color: 'var(--accent)',
                      lineHeight: '1.3',
                      boxSizing: 'border-box',
                    }}>{ev.title}</span>
                  ))}
                  {dayEvents.length > 2 && (
                    <span style={{ fontSize: '10px', color: 'var(--ink2)', paddingLeft: '5px' }}>+{dayEvents.length - 2} ещё</span>
                  )}
                </div>
              </button>
            );
          })}
        </div>
      </Card>

      {/* ── Day detail MODAL (centered portal) ── */}
      {selectedDay && createPortal(
        <div
          style={{
            position: 'fixed', inset: 0, zIndex: 1000,
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            background: 'rgba(0,0,0,0.5)',
            backdropFilter: 'blur(8px)',
            padding: '16px',
          }}
          onMouseDown={() => setSelectedDay(null)}
        >
          <div
            onMouseDown={e => e.stopPropagation()}
            style={{
              background: 'var(--surface, #fff)',
              borderRadius: '24px',
              border: '1px solid var(--border)',
              boxShadow: '0 32px 100px rgba(0,0,0,0.32), 0 0 0 1px rgba(255,255,255,0.08)',
              width: '100%',
              maxWidth: '460px',
              maxHeight: '88vh',
              overflow: 'hidden',
              display: 'flex',
              flexDirection: 'column',
              animation: 'modal-in 0.18s cubic-bezier(0.34,1.56,0.64,1)',
            }}
          >
            {/* Modal header */}
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '20px 22px 14px',
              borderBottom: '1px solid var(--border-thin)',
              flexShrink: 0,
            }}>
              <div>
                <div style={{ fontSize: '17px', fontWeight: 800, letterSpacing: '-0.4px', textTransform: 'capitalize' }}>
                  {new Date(selectedDay + 'T12:00:00').toLocaleDateString('ru-RU', { weekday: 'long', day: 'numeric', month: 'long' })}
                </div>
                <div style={{ fontSize: '12px', color: 'var(--ink2)', marginTop: '3px' }}>
                  {selectedEvents.length === 0 ? 'Нет событий' : `${selectedEvents.length} событи${selectedEvents.length === 1 ? 'е' : selectedEvents.length < 5 ? 'я' : 'й'}`}
                </div>
              </div>
              <button
                onClick={() => setSelectedDay(null)}
                style={{
                  width: 34, height: 34, borderRadius: '50%',
                  border: '1px solid var(--border-thin)',
                  background: 'var(--glass)',
                  display: 'grid', placeItems: 'center',
                  cursor: 'pointer', flexShrink: 0,
                }}
              ><X size={15} /></button>
            </div>

            {/* Scrollable body */}
            <div style={{ overflowY: 'auto', padding: '16px 22px 22px', flex: 1 }}>
              {selectedEvents.length === 0 ? (
                <div style={{ textAlign: 'center', padding: '28px 0 20px' }}>
                  <div style={{ fontSize: 44, marginBottom: 10 }}>📅</div>
                  <p style={{ fontWeight: 700, fontSize: 15 }}>На этот день событий нет</p>
                  <p style={{ fontSize: 12, color: 'var(--ink2)', marginTop: 4 }}>Добавьте событие ниже</p>
                </div>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '18px' }}>
                  {selectedEvents
                    .slice()
                    .sort((a, b) => a.time.localeCompare(b.time))
                    .map(ev => {
                      const color = TYPE_COLORS[ev.type] ?? '#6366f1';
                      const icon = ev.type === 'Work' ? '💼' : ev.type === 'Fitness' ? '🏋️' : ev.type === 'Personal' ? '👤' : ev.type === 'Finance' ? '💰' : '📌';
                      return (
                        <button
                          key={ev.id}
                          onClick={() => { openEvent(ev); setSelectedDay(null); }}
                          style={{
                            display: 'flex', alignItems: 'center', gap: '12px',
                            padding: '13px 14px',
                            borderRadius: '14px',
                            border: `1.5px solid ${color}25`,
                            background: `${color}0e`,
                            cursor: 'pointer', textAlign: 'left', width: '100%',
                            transition: 'background 0.12s',
                          }}
                        >
                          <div style={{
                            width: 42, height: 42, borderRadius: '13px', flexShrink: 0,
                            background: `${color}18`,
                            display: 'grid', placeItems: 'center', fontSize: '20px',
                          }}>{icon}</div>
                          <div style={{ flex: 1, minWidth: 0 }}>
                            <div style={{ fontWeight: 700, fontSize: 14, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{ev.title}</div>
                            <div style={{ fontSize: 12, color: 'var(--ink2)', marginTop: 2 }}>
                              <span style={{ fontWeight: 700, color }}>{ev.time}</span> &nbsp;·&nbsp; {ev.type}
                            </div>
                          </div>
                        </button>
                      );
                    })
                  }
                </div>
              )}

              {/* Add event form inside modal */}
              <div style={{ borderTop: '1px solid var(--border-thin)', paddingTop: '16px', marginTop: selectedEvents.length ? 0 : 8 }}>
                <p style={{ fontSize: 12, fontWeight: 700, color: 'var(--ink2)', marginBottom: 12 }}>Добавить событие</p>
                <EventForm add={add} after={() => setSelectedDay(null)} defaultDate={selectedDay} />
              </div>
            </div>
          </div>
        </div>,
        document.body
      )}

      {/* Always-visible add event card */}
      <Card>
        <SectionTitle title="Добавить событие" />
        <EventForm add={add} />
      </Card>
    </div>
  );
}

function Analytics({ data }: { data: AppData }) {
  const [period, setPeriod] = useState<"day" | "week" | "month" | "year">("week");

  // Helper filters
  const filterByPeriod = <T extends { date?: string; datetime?: string; due?: string; targetDate?: string }>(items: T[]) => {
    return items.filter((item) => {
      const itemDate = item.date || item.due || item.targetDate || item.datetime?.slice(0, 10);
      if (!itemDate) return false;
      if (period === "day") return itemDate === iso(0);
      if (period === "week") return itemDate >= iso(-6);
      if (period === "month") return itemDate >= iso(-29);
      return itemDate >= iso(-364);
    });
  };

  const filterByPrevPeriod = <T extends { date?: string; datetime?: string; due?: string; targetDate?: string }>(items: T[]) => {
    return items.filter((item) => {
      const itemDate = item.date || item.due || item.targetDate || item.datetime?.slice(0, 10);
      if (!itemDate) return false;
      if (period === "day") return itemDate === iso(-1);
      if (period === "week") return itemDate >= iso(-13) && itemDate < iso(-6);
      if (period === "month") return itemDate >= iso(-59) && itemDate < iso(-29);
      return itemDate >= iso(-729) && itemDate < iso(-364);
    });
  };

  const getPercentChange = (cur: number, prev: number) => {
    if (prev === 0) return cur > 0 ? "+100%" : "0%";
    const diff = ((cur - prev) / Math.abs(prev)) * 100;
    const sign = diff > 0 ? "+" : "";
    return `${sign}${Math.round(diff)}%`;
  };

  const getPct = (val: number, goal: number) => goal > 0 ? Math.round((val / goal) * 100) : 0;

  // --- FINANCES DATA ---
  const fTxs = filterByPeriod(data.transactions);
  const pTxs = filterByPrevPeriod(data.transactions);

  const inc = fTxs.filter(t => t.type === "income").reduce((s, t) => s + t.amount, 0);
  const exp = fTxs.filter(t => t.type === "expense").reduce((s, t) => s + t.amount, 0);
  const bal = inc - exp;

  const prevInc = pTxs.filter(t => t.type === "income").reduce((s, t) => s + t.amount, 0);
  const prevExp = pTxs.filter(t => t.type === "expense").reduce((s, t) => s + t.amount, 0);
  const prevBal = prevInc - prevExp;

  const incomeChange = getPercentChange(inc, prevInc);
  const expenseChange = getPercentChange(exp, prevExp);
  const balanceChange = getPercentChange(bal, prevBal);

  const expList = fTxs.filter(t => t.type === "expense");
  const categoriesData = Array.from(new Set(expList.map(t => t.category))).map(cat => ({ name: cat, value: expList.filter(t => t.category === cat).reduce((s, t) => s + t.amount, 0) })).sort((a, b) => b.value - a.value);
  const totalExpenseVal = categoriesData.reduce((s, c) => s + c.value, 0) || 1;
  const categoriesWithPct = categoriesData.map(c => ({ ...c, pct: Math.round((c.value / totalExpenseVal) * 100) }));

  const maxExpense = expList.length ? Math.max(...expList.map(t => t.amount)) : 0;
  const peakTxs = [...fTxs].sort((a,b) => b.amount - a.amount).slice(0, 3);

  const getFinanceChartData = () => {
    if (period === "day") {
      return Array.from({ length: 24 }, (_, h) => {
        const label = `${h}:00`;
        const hourTxs = fTxs.filter(t => parseInt(t.time?.slice(0, 2) ?? "0") === h);
        const income = hourTxs.filter(t => t.type === "income").reduce((s, t) => s + t.amount, 0);
        const expense = hourTxs.filter(t => t.type === "expense").reduce((s, t) => s + t.amount, 0);
        return { label, income, expense };
      });
    }
    if (period === "week") {
      return Array.from({ length: 7 }, (_, i) => {
        const d = iso(i - 6);
        const label = new Date(d).toLocaleDateString("ru-RU", { weekday: "short" });
        const dayTxs = fTxs.filter(t => t.date === d);
        const income = dayTxs.filter(t => t.type === "income").reduce((s, t) => s + t.amount, 0);
        const expense = dayTxs.filter(t => t.type === "expense").reduce((s, t) => s + t.amount, 0);
        return { label, income, expense };
      });
    }
    if (period === "month") {
      return Array.from({ length: 30 }, (_, i) => {
        const d = iso(i - 29);
        const label = d.slice(8, 10);
        const dayTxs = fTxs.filter(t => t.date === d);
        const income = dayTxs.filter(t => t.type === "income").reduce((s, t) => s + t.amount, 0);
        const expense = dayTxs.filter(t => t.type === "expense").reduce((s, t) => s + t.amount, 0);
        return { label, income, expense };
      });
    }
    return Array.from({ length: 12 }, (_, i) => {
      const d = new Date();
      d.setMonth(d.getMonth() - (11 - i));
      const label = d.toLocaleDateString("ru-RU", { month: "short" });
      const yearMonth = d.toISOString().slice(0, 7);
      const monthTxs = fTxs.filter(t => t.date.startsWith(yearMonth));
      const income = monthTxs.filter(t => t.type === "income").reduce((s, t) => s + t.amount, 0);
      const expense = monthTxs.filter(t => t.type === "expense").reduce((s, t) => s + t.amount, 0);
      return { label, income, expense };
    });
  };

  const financeChartData = getFinanceChartData();

  // --- NUTRITION DATA ---
  const fMeals = filterByPeriod(data.meals);
  const pMeals = filterByPrevPeriod(data.meals);
  const curDays = Array.from(new Set(fMeals.map(m => m.date))).length || 1;
  const prevDays = Array.from(new Set(pMeals.map(m => m.date))).length || 1;

  const totalCals = fMeals.reduce((s, m) => s + m.calories, 0);
  const avgCals = Math.round(totalCals / curDays);
  const prevAvgCals = Math.round(pMeals.reduce((s, m) => s + m.calories, 0) / prevDays);
  const calsChange = getPercentChange(avgCals, prevAvgCals);

  const avgProt = Math.round(fMeals.reduce((s, m) => s + m.protein, 0) / curDays);
  const avgFat = Math.round(fMeals.reduce((s, m) => s + m.fat, 0) / curDays);
  const avgCarb = Math.round(fMeals.reduce((s, m) => s + m.carbs, 0) / curDays);

  const calGoal = Number(data.settings.caloriesGoal) || 2200;
  const protGoal = Number(data.settings.proteinGoal) || 150;
  const fatGoal = Number(data.settings.fatGoal) || 70;
  const carbGoal = Number(data.settings.carbsGoal) || 250;

  const safeDaysCount = Array.from(new Set(fMeals.map(m => m.date))).filter(d => {
    const dayMeals = fMeals.filter(m => m.date === d);
    const dayCals = dayMeals.reduce((s, m) => s + m.calories, 0);
    return Math.abs(dayCals - calGoal) <= 250;
  }).length;
  const targetSuccessPct = fMeals.length ? Math.round((safeDaysCount / Array.from(new Set(fMeals.map(m => m.date))).length) * 100) : 0;

  const getNutritionChartData = () => {
    if (period === "day") {
      const mealTypes = ["Завтрак", "Обед", "Ужин", "Перекус"];
      return mealTypes.map((type) => {
        const calories = fMeals.filter(m => m.mealType === type).reduce((s, m) => s + m.calories, 0);
        return { label: type, calories, goal: calGoal };
      });
    }
    if (period === "week") {
      return Array.from({ length: 7 }, (_, i) => {
        const d = iso(i - 6);
        const label = new Date(d).toLocaleDateString("ru-RU", { weekday: "short" });
        const calories = fMeals.filter(m => m.date === d).reduce((s, m) => s + m.calories, 0);
        return { label, calories, goal: calGoal };
      });
    }
    if (period === "month") {
      return Array.from({ length: 30 }, (_, i) => {
        const d = iso(i - 29);
        const label = d.slice(8, 10);
        const calories = fMeals.filter(m => m.date === d).reduce((s, m) => s + m.calories, 0);
        return { label, calories, goal: calGoal };
      });
    }
    return Array.from({ length: 12 }, (_, i) => {
      const d = new Date();
      d.setMonth(d.getMonth() - (11 - i));
      const label = d.toLocaleDateString("ru-RU", { month: "short" });
      const yearMonth = d.toISOString().slice(0, 7);
      const monthMeals = fMeals.filter(m => m.date.startsWith(yearMonth));
      const uniqueMDays = Array.from(new Set(monthMeals.map(m => m.date))).length || 1;
      const calories = Math.round(monthMeals.reduce((s, m) => s + m.calories, 0) / uniqueMDays);
      return { label, calories, goal: calGoal };
    });
  };

  const nutritionChartData = getNutritionChartData();

  // --- HEALTH & BODY DATA ---
  const fHealth = filterByPeriod(data.health);
  const pHealth = filterByPrevPeriod(data.health);
  const fBody = filterByPeriod(data.bodyLogs ?? []);

  const avgSleep = fHealth.length ? Math.round((fHealth.reduce((s, h) => s + h.sleep, 0) / fHealth.length) * 10) / 10 : 0;
  const prevAvgSleep = pHealth.length ? Math.round((pHealth.reduce((s, h) => s + h.sleep, 0) / pHealth.length) * 10) / 10 : 0;
  const sleepChange = getPercentChange(avgSleep, prevAvgSleep);

  const avgWater = fHealth.length ? Math.round((fHealth.reduce((s, h) => s + h.water, 0) / fHealth.length) * 10) / 10 : 0;
  const prevAvgWater = pHealth.length ? Math.round((pHealth.reduce((s, h) => s + h.water, 0) / pHealth.length) * 10) / 10 : 0;
  const waterChange = getPercentChange(avgWater, prevAvgWater);

  const latestBody = [...(data.bodyLogs ?? [])].sort((a,b) => b.date.localeCompare(a.date))[0];
  const startBody = fBody.length ? [...fBody].sort((a,b) => a.date.localeCompare(b.date))[0] : latestBody;
  const weightDiff = latestBody && startBody ? Math.round((latestBody.weight - startBody.weight) * 10) / 10 : 0;

  const getHealthChartData = () => {
    if (period === "day") {
      return [
        {
          label: "Сегодня",
          weight: latestBody?.weight || null,
          sleep: fHealth[0]?.sleep || null,
        }
      ];
    }
    if (period === "week") {
      return Array.from({ length: 7 }, (_, i) => {
        const d = iso(i - 6);
        const label = new Date(d).toLocaleDateString("ru-RU", { weekday: "short" });
        const bodyLog = fBody.find(b => b.date === d);
        const healthLog = fHealth.find(h => h.date === d);
        return {
          label,
          weight: bodyLog?.weight || latestBody?.weight || null,
          sleep: healthLog?.sleep || null,
        };
      });
    }
    if (period === "month") {
      return Array.from({ length: 30 }, (_, i) => {
        const d = iso(i - 29);
        const label = d.slice(8, 10);
        const bodyLog = fBody.find(b => b.date === d);
        const healthLog = fHealth.find(h => h.date === d);
        return {
          label,
          weight: bodyLog?.weight || latestBody?.weight || null,
          sleep: healthLog?.sleep || null,
        };
      });
    }
    return Array.from({ length: 12 }, (_, i) => {
      const d = new Date();
      d.setMonth(d.getMonth() - (11 - i));
      const label = d.toLocaleDateString("ru-RU", { month: "short" });
      const yearMonth = d.toISOString().slice(0, 7);
      const mBody = fBody.filter(b => b.date.startsWith(yearMonth));
      const mHealth = fHealth.filter(h => h.date.startsWith(yearMonth));
      const weightVal = mBody.length ? Math.round((mBody.reduce((s, b) => s + b.weight, 0) / mBody.length) * 10) / 10 : (latestBody?.weight || null);
      const sleepVal = mHealth.length ? Math.round((mHealth.reduce((s, h) => s + h.sleep, 0) / mHealth.length) * 10) / 10 : null;
      return { label, weight: weightVal, sleep: sleepVal };
    });
  };

  const healthChartData = getHealthChartData();

  // --- MOOD DATA ---
  const moodLog = data.moodLog ?? [];
  const fMoods = filterByPeriod(moodLog);
  const pMoods = filterByPrevPeriod(moodLog);
  const avgMoodVal = fMoods.length ? moodAvg(fMoods) : 0;
  const prevAvgMoodVal = pMoods.length ? moodAvg(pMoods) : 0;
  const moodChange = getPercentChange(avgMoodVal, prevAvgMoodVal);

  const labelCounts = fMoods.reduce((acc, entry) => {
    acc[entry.label] = (acc[entry.label] || 0) + 1;
    return acc;
  }, {} as Record<string, number>);

  const moodDistribution = Object.entries(labelCounts).map(([name, value]) => ({ name, value })).sort((a,b) => b.value - a.value);
  const domMood = moodDistribution[0]?.name ?? "—";

  const getMoodChartData = () => {
    if (period === "day") {
      return Array.from({ length: 24 }, (_, h) => {
        const label = `${h}:00`;
        const hrMoods = fMoods.filter(m => parseInt(m.datetime?.slice(11, 13) ?? "0") === h);
        return { label, mood: hrMoods.length ? moodAvg(hrMoods) : null };
      }).filter(d => d.mood !== null);
    }
    if (period === "week") {
      return Array.from({ length: 7 }, (_, i) => {
        const d = iso(i - 6);
        const label = new Date(d).toLocaleDateString("ru-RU", { weekday: "short" });
        const dayMoods = moodLog.filter(m => m.datetime?.slice(0, 10) === d);
        return { label, mood: dayMoods.length ? moodAvg(dayMoods) : null };
      });
    }
    if (period === "month") {
      return Array.from({ length: 30 }, (_, i) => {
        const d = iso(i - 29);
        const label = d.slice(8, 10);
        const dayMoods = moodLog.filter(m => m.datetime?.slice(0, 10) === d);
        return { label, mood: dayMoods.length ? moodAvg(dayMoods) : null };
      });
    }
    return Array.from({ length: 12 }, (_, i) => {
      const d = new Date();
      d.setMonth(d.getMonth() - (11 - i));
      const label = d.toLocaleDateString("ru-RU", { month: "short" });
      const yearMonth = d.toISOString().slice(0, 7);
      const monthMoods = moodLog.filter(m => m.datetime?.startsWith(yearMonth));
      return { label, mood: monthMoods.length ? moodAvg(monthMoods) : null };
    });
  };

  const moodChartData = getMoodChartData();

  // --- TASKS & GOALS ---
  const fTasks = filterByPeriod(data.tasks);
  const tasksDone = fTasks.filter(t => t.status === "done").length;
  const tasksActive = fTasks.filter(t => t.status !== "done").length;
  const tasksRate = fTasks.length ? Math.round((tasksDone / fTasks.length) * 100) : 0;

  const curGoals = data.goals ?? [];
  const goalsTotal = curGoals.length;
  const goalsCompleted = curGoals.filter(g => g.status === "completed").length;
  const goalsOnTrack = curGoals.filter(g => g.status === "on track").length;

  const COLORS = ["#00F2FE", "#9B51E0", "#FF4C60", "#F2C94C", "#27AE60"];

  return (
    <div className="space-y-6">
      {/* Dynamic Segmented Period Selector */}
      <div className="flex flex-col md:flex-row justify-between items-start md:items-center bg-[var(--glass)] border border-[var(--border)] rounded-2xl px-5 py-3.5 shadow-xl gap-4">
        <div>
          <h2 className="text-xl font-extrabold flex items-center gap-2">
            <Sparkles size={20} className="text-accent" />
            Аналитический Центр
          </h2>
          <p className="text-xs text-slate-500 font-medium">Консолидированные метрики вашей продуктивности, финансов и здоровья</p>
        </div>
        <div className="flex rounded-xl border border-[var(--border)] bg-[var(--glass-thin)] p-0.5 self-end">
          {(["day", "week", "month", "year"] as const).map((pKey) => {
            const label = pKey === "day" ? "День" : pKey === "week" ? "Неделя" : pKey === "month" ? "Месяц" : "Год";
            return (
              <button
                key={pKey}
                onClick={() => setPeriod(pKey)}
                className="px-4 py-1.5 rounded-lg text-xs font-bold transition-all duration-200"
                style={{
                  background: period === pKey ? "var(--accent)" : "transparent",
                  color: period === pKey ? "#fff" : "var(--ink2)",
                  boxShadow: period === pKey ? "var(--shadow-soft)" : "none",
                }}
              >
                {label}
              </button>
            );
          })}
        </div>
      </div>

      {/* ─── FINANCE ANALYTICS ─── */}
      <Card className="xl:col-span-3">
        <SectionTitle title="Финансовая аналитика" sub="Баланс, динамика доходов и расходов" />
        {fTxs.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-slate-400 dark:text-slate-500">
            <Wallet size={36} className="opacity-40 mb-2" />
            <p className="text-sm font-medium">Нет финансовых операций за выбранный период</p>
          </div>
        ) : (
          <div className="grid gap-6 grid-cols-1 xl:grid-cols-3">
            {/* Left Col: Area Chart */}
            <div className="xl:col-span-2 space-y-4">
              <div className="grid grid-cols-3 gap-4">
                <div className="p-3 bg-[var(--glass-thin)] rounded-xl border border-[var(--border-thin)]">
                  <span className="text-[10px] uppercase font-bold text-slate-400">Доходы</span>
                  <div className="text-lg font-bold text-cyan-500 mt-1">{money.format(inc)}</div>
                  <span className={`text-[10px] font-bold ${inc >= prevInc ? "text-emerald-500" : "text-rose-500"}`}>{incomeChange} vs п.п.</span>
                </div>
                <div className="p-3 bg-[var(--glass-thin)] rounded-xl border border-[var(--border-thin)]">
                  <span className="text-[10px] uppercase font-bold text-slate-400">Расходы</span>
                  <div className="text-lg font-bold text-purple-500 mt-1">{money.format(exp)}</div>
                  <span className={`text-[10px] font-bold ${exp <= prevExp ? "text-emerald-500" : "text-rose-500"}`}>{expenseChange} vs п.п.</span>
                </div>
                <div className="p-3 bg-[var(--glass-thin)] rounded-xl border border-[var(--border-thin)]">
                  <span className="text-[10px] uppercase font-bold text-slate-400">Баланс</span>
                  <div className="text-lg font-bold text-[var(--text-balance-raw)] mt-1">{money.format(bal)}</div>
                  <span className={`text-[10px] font-bold ${bal >= prevBal ? "text-emerald-500" : "text-rose-500"}`}>{balanceChange} vs п.п.</span>
                </div>
              </div>
              <ChartWrap>
                <AreaChart data={financeChartData}>
                  <defs>
                    <linearGradient id="colorIncome" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#00F2FE" stopOpacity={0.2}/>
                      <stop offset="95%" stopColor="#00F2FE" stopOpacity={0}/>
                    </linearGradient>
                    <linearGradient id="colorExpense" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#9B51E0" stopOpacity={0.2}/>
                      <stop offset="95%" stopColor="#9B51E0" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <CartesianGrid vertical={false} stroke="var(--grid-line)" />
                  <XAxis dataKey="label" tickLine={false} tick={{ fill: "var(--ink2)", fontSize: 11 }} />
                  <YAxis hide />
                  <Tooltip formatter={(value) => money.format(Number(value))} />
                  <Area type="monotone" name="Доход" dataKey="income" stroke="#00F2FE" strokeWidth={3} fillOpacity={1} fill="url(#colorIncome)" />
                  <Area type="monotone" name="Расход" dataKey="expense" stroke="#9B51E0" strokeWidth={3} fillOpacity={1} fill="url(#colorExpense)" />
                </AreaChart>
              </ChartWrap>
            </div>
            {/* Right Col: Pie Chart */}
            <div className="space-y-4 flex flex-col justify-between">
              <div>
                <h4 className="text-xs uppercase font-bold text-slate-400 mb-3">Категории расходов</h4>
                {categoriesWithPct.length === 0 ? (
                  <p className="text-xs text-slate-400">Нет трат в этом периоде</p>
                ) : (
                  <div className="flex flex-col items-center gap-3">
                    <ChartWrap small>
                      <PieChart>
                        <Pie data={categoriesWithPct} dataKey="value" nameKey="name" cx="50%" cy="50%" innerRadius={42} outerRadius={68} paddingAngle={3}>
                          {categoriesWithPct.map((_, index) => (
                            <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                          ))}
                        </Pie>
                        <Tooltip formatter={(value) => money.format(Number(value))} />
                      </PieChart>
                    </ChartWrap>
                    <div className="w-full space-y-1.5">
                      {categoriesWithPct.slice(0, 4).map((c, i) => (
                        <div key={c.name} className="flex justify-between items-center text-xs">
                          <span className="flex items-center gap-1.5 font-medium">
                            <span className="w-2.5 h-2.5 rounded-full" style={{ background: COLORS[i % COLORS.length] }} />
                            {c.name}
                          </span>
                          <span className="font-bold text-slate-500">{c.pct}% ({money.format(c.value)})</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
              <div className="p-3 bg-[var(--glass-thin)] border border-[var(--border-thin)] rounded-xl">
                <span className="text-[10px] uppercase font-bold text-slate-400">Пиковые операции:</span>
                <div className="mt-2 space-y-1">
                  {peakTxs.map((t, idx) => (
                    <div key={t.id} className="flex justify-between items-center text-xs">
                      <span className="text-slate-500 font-medium truncate max-w-[130px]">{idx + 1}. {t.title}</span>
                      <span className={`font-bold ${t.type === "income" ? "text-cyan-500" : "text-purple-500"}`}>
                        {t.type === "income" ? "+" : "-"}{money.format(t.amount)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          </div>
        )}
      </Card>

      {/* ─── NUTRITION & HEALTH ROW ─── */}
      <div className="grid gap-6 grid-cols-1 xl:grid-cols-2">
        {/* NUTRITION CARD */}
        <Card>
          <SectionTitle title="Аналитика питания" sub="Калории и баланс макронутриентов" />
          {fMeals.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-slate-400 dark:text-slate-500">
              <Utensils size={36} className="opacity-40 mb-2" />
              <p className="text-sm font-medium">Нет записей о приёмах пищи</p>
            </div>
          ) : (
            <div className="space-y-5">
              <div className="grid grid-cols-3 gap-3">
                <div className="p-3 bg-[var(--glass-thin)] rounded-xl border border-[var(--border-thin)]">
                  <span className="text-[10px] uppercase font-bold text-slate-400">Средние кал.</span>
                  <div className="text-lg font-bold text-orange-500 mt-1">{avgCals} ккал</div>
                  <span className={`text-[10px] font-bold ${avgCals <= calGoal ? "text-emerald-500" : "text-rose-500"}`}>{calsChange} vs п.п.</span>
                </div>
                <div className="p-3 bg-[var(--glass-thin)] rounded-xl border border-[var(--border-thin)]">
                  <span className="text-[10px] uppercase font-bold text-slate-400">В рамках цели</span>
                  <div className="text-lg font-bold text-emerald-500 mt-1">{targetSuccessPct}% дней</div>
                  <span className="text-[10px] text-slate-400 font-medium">цель {calGoal} ккал</span>
                </div>
                <div className="p-3 bg-[var(--glass-thin)] rounded-xl border border-[var(--border-thin)]">
                  <span className="text-[10px] uppercase font-bold text-slate-400">Б / Ж / У</span>
                  <div className="text-xs font-bold text-slate-600 dark:text-slate-300 mt-1.5">
                    {avgProt}г / {avgFat}г / {avgCarb}г
                  </div>
                </div>
              </div>

              <ChartWrap small>
                <BarChart data={nutritionChartData}>
                  <CartesianGrid vertical={false} stroke="var(--grid-line)" />
                  <XAxis dataKey="label" tickLine={false} tick={{ fill: "var(--ink2)", fontSize: 11 }} />
                  <YAxis hide />
                  <Tooltip />
                  <Bar name="Калории" dataKey="calories" fill="var(--accent)" radius={[5, 5, 0, 0]} />
                </BarChart>
              </ChartWrap>

              {/* Macro target meters */}
              <div className="space-y-2">
                {[
                  ["Белки", avgProt, protGoal, "bg-emerald-400"],
                  ["Жиры", avgFat, fatGoal, "bg-blue-400"],
                  ["Углеводы", avgCarb, carbGoal, "bg-orange-400"]
                ].map(([label, val, goal, color]) => {
                  const pct = getPct(val as number, goal as number);
                  return (
                    <div key={label as string} className="space-y-1">
                      <div className="flex justify-between text-xs">
                        <span className="font-semibold">{label as string} (среднее)</span>
                        <span className="font-bold text-slate-500">{val as number}г / {goal as number}г ({pct}%)</span>
                      </div>
                      <Progress value={pct} color={color as string} />
                    </div>
                  );
                })}
              </div>
            </div>
          )}
        </Card>

        {/* HEALTH & BODY CARD */}
        <Card>
          <SectionTitle title="Здоровье и Тело" sub="Динамика веса, параметры сна и состава тела" />
          {fHealth.length === 0 && fBody.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-slate-400 dark:text-slate-500">
              <Activity size={36} className="opacity-40 mb-2" />
              <p className="text-sm font-medium">Нет записей о здоровье и теле</p>
            </div>
          ) : (
            <div className="space-y-5">
              <div className="grid grid-cols-3 gap-3">
                <div className="p-3 bg-[var(--glass-thin)] rounded-xl border border-[var(--border-thin)]">
                  <span className="text-[10px] uppercase font-bold text-slate-400">Сон (средний)</span>
                  <div className="text-lg font-bold text-indigo-500 mt-1">{avgSleep} ч</div>
                  <span className={`text-[10px] font-bold ${avgSleep >= (Number(data.settings.sleepGoal) || 7.5) ? "text-emerald-500" : "text-rose-500"}`}>{sleepChange} vs п.п.</span>
                </div>
                <div className="p-3 bg-[var(--glass-thin)] rounded-xl border border-[var(--border-thin)]">
                  <span className="text-[10px] uppercase font-bold text-slate-400">Вода (средняя)</span>
                  <div className="text-lg font-bold text-blue-500 mt-1">{avgWater} л</div>
                  <span className={`text-[10px] font-bold ${avgWater >= (Number(data.settings.waterGoal) || 2.5) ? "text-emerald-500" : "text-rose-500"}`}>{waterChange} vs п.п.</span>
                </div>
                <div className="p-3 bg-[var(--glass-thin)] rounded-xl border border-[var(--border-thin)]">
                  <span className="text-[10px] uppercase font-bold text-slate-400">Вес</span>
                  <div className="text-lg font-bold text-rose-500 mt-1">
                    {latestBody ? `${latestBody.weight} кг` : "—"}
                  </div>
                  <span className={`text-[10px] font-bold ${weightDiff <= 0 ? "text-emerald-500" : "text-amber-500"}`}>
                    {weightDiff > 0 ? `+${weightDiff}` : weightDiff} кг
                  </span>
                </div>
              </div>

              <ChartWrap small>
                <LineChart data={healthChartData}>
                  <CartesianGrid vertical={false} stroke="var(--grid-line)" />
                  <XAxis dataKey="label" tickLine={false} tick={{ fill: "var(--ink2)", fontSize: 11 }} />
                  <YAxis hide />
                  <Tooltip />
                  <Line type="monotone" name="Вес (кг)" dataKey="weight" stroke="#EC4899" strokeWidth={3} dot={{ r: 3 }} />
                  <Line type="monotone" name="Сон (ч)" dataKey="sleep" stroke="#6366F1" strokeWidth={3} dot={{ r: 3 }} />
                </LineChart>
              </ChartWrap>

              {/* Body parameters grid */}
              {latestBody && (
                <div className="grid grid-cols-4 gap-2 bg-[var(--glass-thin)] p-2.5 rounded-xl border border-[var(--border-thin)] text-center text-xs">
                  <div>
                    <div className="text-slate-400 font-bold uppercase text-[9px]">ИМТ</div>
                    <div className="mt-0.5 font-bold text-indigo-500">{latestBody.bmi}</div>
                  </div>
                  <div>
                    <div className="text-slate-400 font-bold uppercase text-[9px]">Жир %</div>
                    <div className="mt-0.5 font-bold text-pink-500">{latestBody.fatPct}%</div>
                  </div>
                  <div>
                    <div className="text-slate-400 font-bold uppercase text-[9px]">Мышцы %</div>
                    <div className="mt-0.5 font-bold text-emerald-500">{latestBody.musclePct}%</div>
                  </div>
                  <div>
                    <div className="text-slate-400 font-bold uppercase text-[9px]">Возраст тела</div>
                    <div className="mt-0.5 font-bold text-amber-500">{latestBody.bodyAge}</div>
                  </div>
                </div>
              )}
            </div>
          )}
        </Card>
      </div>

      {/* ─── MOOD & TASKS/GOALS ROW ─── */}
      <div className="grid gap-6 grid-cols-1 xl:grid-cols-2">
        {/* MOOD CARD */}
        <Card>
          <SectionTitle title="Аналитика настроения" sub="Динамика психологического состояния" />
          {fMoods.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-slate-400 dark:text-slate-500">
              <Smile size={36} className="opacity-40 mb-2" />
              <p className="text-sm font-medium">Нет записей о настроении</p>
            </div>
          ) : (
            <div className="space-y-5">
              <div className="grid grid-cols-3 gap-3">
                <div className="p-3 bg-[var(--glass-thin)] rounded-xl border border-[var(--border-thin)]">
                  <span className="text-[10px] uppercase font-bold text-slate-400">Среднее</span>
                  <div className="text-lg font-bold text-indigo-500 mt-1">{avgMoodVal} / 10</div>
                  <span className={`text-[10px] font-bold ${avgMoodVal >= prevAvgMoodVal ? "text-emerald-500" : "text-rose-500"}`}>{moodChange} vs п.п.</span>
                </div>
                <div className="p-3 bg-[var(--glass-thin)] rounded-xl border border-[var(--border-thin)]">
                  <span className="text-[10px] uppercase font-bold text-slate-400">Доминантное</span>
                  <div className="text-lg font-bold text-pink-500 mt-1">{domMood}</div>
                  <span className="text-[10px] text-slate-400 font-medium">из {fMoods.length} записей</span>
                </div>
                <div className="p-3 bg-[var(--glass-thin)] rounded-xl border border-[var(--border-thin)]">
                  <span className="text-[10px] uppercase font-bold text-slate-400">Стабильность</span>
                  <div className="text-lg font-bold text-emerald-500 mt-1">
                    {fMoods.length > 2 ? "Высокая" : "Мало данных"}
                  </div>
                </div>
              </div>

              <ChartWrap small>
                <AreaChart data={moodChartData}>
                  <defs>
                    <linearGradient id="colorMood" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#4F46E5" stopOpacity={0.2}/>
                      <stop offset="95%" stopColor="#4F46E5" stopOpacity={0}/>
                    </linearGradient>
                  </defs>
                  <CartesianGrid vertical={false} stroke="var(--grid-line)" />
                  <XAxis dataKey="label" tickLine={false} tick={{ fill: "var(--ink2)", fontSize: 11 }} />
                  <YAxis hide domain={[1, 10]} />
                  <Tooltip />
                  <Area type="monotone" name="Настроение" dataKey="mood" stroke="#4F46E5" strokeWidth={3} fillOpacity={1} fill="url(#colorMood)" dot={{ r: 3 }} />
                </AreaChart>
              </ChartWrap>

              {/* Mood states distribution list */}
              <div className="space-y-1.5">
                <h4 className="text-[10px] uppercase font-bold text-slate-400">Частота состояний:</h4>
                <div className="grid grid-cols-2 gap-2 text-xs">
                  {moodDistribution.slice(0, 4).map(({ name, value }) => (
                    <div key={name} className="flex justify-between items-center p-2 bg-[var(--glass-thin)] rounded-lg border border-[var(--border-thin)]">
                      <span className="font-semibold">{name}</span>
                      <span className="font-bold text-slate-500">{value} раз</span>
                    </div>
                  ))}
                </div>
              </div>
            </div>
          )}
        </Card>

        {/* TASKS & GOALS CARD */}
        <Card>
          <SectionTitle title="Задачи и Цели" sub="Выполнение планов и прогресс по целям" />
          <div className="space-y-5">
            <div className="grid grid-cols-3 gap-3">
              <div className="p-3 bg-[var(--glass-thin)] rounded-xl border border-[var(--border-thin)]">
                <span className="text-[10px] uppercase font-bold text-slate-400">Задачи (%)</span>
                <div className="text-lg font-bold text-indigo-500 mt-1">{tasksRate}%</div>
                <span className="text-[10px] text-slate-400 font-medium">{tasksDone} вып. / {tasksActive} в работе</span>
              </div>
              <div className="p-3 bg-[var(--glass-thin)] rounded-xl border border-[var(--border-thin)]">
                <span className="text-[10px] uppercase font-bold text-slate-400">Всего целей</span>
                <div className="text-lg font-bold text-emerald-500 mt-1">{goalsTotal}</div>
                <span className="text-[10px] text-slate-400 font-medium">{goalsCompleted} завершено</span>
              </div>
              <div className="p-3 bg-[var(--glass-thin)] rounded-xl border border-[var(--border-thin)]">
                <span className="text-[10px] uppercase font-bold text-slate-400">Цели "On Track"</span>
                <div className="text-lg font-bold text-amber-500 mt-1">{goalsOnTrack}</div>
                <span className="text-[10px] text-slate-400 font-medium">из {goalsTotal} active</span>
              </div>
            </div>

            {curGoals.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-slate-400 dark:text-slate-500">
                <Goal size={36} className="opacity-40 mb-2" />
                <p className="text-sm font-medium">Нет активных целей</p>
              </div>
            ) : (
              <div className="space-y-3">
                <h4 className="text-[10px] uppercase font-bold text-slate-400">Текущий прогресс целей:</h4>
                <div className="space-y-3 max-h-[220px] overflow-y-auto pr-1.5 custom-scrollbar font-medium">
                  {curGoals.map((g) => (
                    <div key={g.id} className="p-3 bg-[var(--glass-thin)] rounded-xl border border-[var(--border-thin)] space-y-1.5">
                      <div className="flex justify-between items-center text-xs">
                        <span className="font-bold text-slate-700 dark:text-slate-200">{g.title}</span>
                        <span className={`badge ${g.status === "on track" ? "good" : g.status === "completed" ? "green" : "warn"}`}>
                          {g.status}
                        </span>
                      </div>
                      <Progress value={g.progress} />
                      <div className="flex justify-between text-[10px] text-slate-500 font-medium">
                        <span>Прогресс: {g.progress}%</span>
                        <span>До: {g.targetDate}</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </Card>
      </div>
    </div>
  );
}

// ─── Mood helpers ────────────────────────────────────────────────
const MOOD_COLOR = (v: number) => v >= 8 ? "var(--mood-best)" : v >= 6 ? "var(--mood-good)" : v >= 4 ? "var(--mood-neutral)" : "var(--mood-low)";
const MOOD_BG = (v: number) => v >= 8 ? "var(--mood-best-glow)" : v >= 6 ? "var(--mood-good-glow)" : v >= 4 ? "var(--mood-neutral-glow)" : "var(--mood-low-glow)";
const MOOD_CAT = (v: number) => v >= 6 ? "Хорошее" : v >= 3 ? "Нейтральное" : "Плохое";

function moodAvg(entries: MoodEntry[]): number {
  if (!entries.length) return 0;
  return Math.round((entries.reduce((s, e) => s + e.value, 0) / entries.length) * 10) / 10;
}

function MoodPage({ data, setData }: { data: AppData; setData: React.Dispatch<React.SetStateAction<AppData>> }) {
  const [viewPeriod, setViewPeriod] = useState<"today" | "week" | "month" | "year">("week");
  const moodLog = data.moodLog ?? [];

  // ── Today: group by hour ──────────────────────────────
  const todayEntries = moodLog.filter((e) => e.datetime.startsWith(iso(0)));
  const hourlyData = Array.from({ length: 24 }, (_, h) => {
    const hrs = todayEntries.filter((e) => Number(e.datetime.slice(11, 13)) === h);
    return { hour: `${h}:00`, value: hrs.length ? moodAvg(hrs) : null, count: hrs.length };
  }).filter((d) => d.value !== null);

  // ── Week: avg per day ─────────────────────────────────
  const weekData = Array.from({ length: 7 }, (_, i) => {
    const date = iso(i - 6);
    const entries = moodLog.filter((e) => e.datetime.startsWith(date));
    return { date: date.slice(5), value: moodAvg(entries), count: entries.length };
  });

  // ── Month: avg per day for last 30 days ───────────────
  const monthData = Array.from({ length: 30 }, (_, i) => {
    const date = iso(i - 29);
    const entries = moodLog.filter((e) => e.datetime.startsWith(date));
    return { date: date.slice(5), value: moodAvg(entries) || null };
  }).filter((d) => d.value);

  // ── Year: avg per calendar month ─────────────────────
  const MONTHS_RU = ["Янв","Фев","Мар","Апр","Май","Июн","Июл","Авг","Сен","Окт","Ноя","Дек"];
  const yearNow = iso(0).slice(0, 4);
  const yearData = Array.from({ length: 12 }, (_, i) => {
    const month = String(i + 1).padStart(2, "0");
    const entries = moodLog.filter((e) => e.datetime.startsWith(`${yearNow}-${month}`));
    return { month: MONTHS_RU[i], value: entries.length ? moodAvg(entries) : null, count: entries.length };
  });

  // ── Chart data by current view ────────────────────────
  const chartData =
    viewPeriod === "today" ? hourlyData.map((d) => ({ label: d.hour, value: d.value })) :
    viewPeriod === "week"  ? weekData.map((d) => ({ label: d.date, value: d.value })) :
    viewPeriod === "month" ? monthData.map((d) => ({ label: d.date, value: d.value })) :
                             yearData.map((d) => ({ label: d.month, value: d.value }));

  // ── Frequency distribution ────────────────────────────
  const freqData = MOOD_EMOJIS.map(([val, emoji, label]) => ({
    name: `${emoji} ${label}`, value: val,
    count: moodLog.filter((e) => e.value === val).length,
  })).filter((d) => d.count > 0);

  // ── Good / Neutral / Bad breakdown ───────────────────
  const total = moodLog.length;
  const goodCount = moodLog.filter((e) => e.value >= 6).length;
  const neutralCount = moodLog.filter((e) => e.value >= 3 && e.value < 6).length;
  const badCount = moodLog.filter((e) => e.value < 3).length;
  const pieData = [
    { name: "Хорошее", value: goodCount, fill: "#30D158" },
    { name: "Нейтральное", value: neutralCount, fill: "#FF9F0A" },
    { name: "Плохое", value: badCount, fill: "#FF453A" },
  ].filter((d) => d.value > 0);

  // ── Summary stats ─────────────────────────────────────
  const overallAvg = moodAvg(moodLog);
  const todayAvg = moodAvg(todayEntries);
  const bestEmoji = MOOD_EMOJIS.reduce((best, cur) =>
    moodLog.filter((e) => e.value === cur[0]).length > moodLog.filter((e) => e.value === best[0]).length ? cur : best
  , MOOD_EMOJIS[0]);

  const handleMoodLog = (val: number) => {
    const moodLabel = MOOD_EMOJIS.find(([v]) => v === val)?.[2] ?? "Норм";
    setData((p) => ({ ...p, moodLog: [{ id: id(), datetime: localDatetime(), value: val, label: moodLabel }, ...(p.moodLog ?? [])] }));
  };
  const currentMood = moodLog[0]?.value ?? 0;

  const PERIOD_LABELS = { today: "Сегодня", week: "7 дней", month: "30 дней", year: "Год" };

  return (
    <PageGrid>
      {/* Quick mood log */}
      <div className="card xl:col-span-3 !p-5 shadow-soft">
        <div className="flex items-center justify-between mb-4">
          <div>
            <h2 className="text-base font-semibold">Отметить настроение</h2>
            <p className="text-sm text-slate-500 mt-0.5">Сегодня {todayEntries.length} записей · Среднее {todayAvg > 0 ? todayAvg : "—"}/9</p>
          </div>
          {moodLog[0] && (
            <div className="flex items-center gap-2.5 rounded-xl border border-[var(--border)] bg-[var(--glass-thin)] px-3 py-2 text-sm font-semibold">
              <span style={{ fontSize: 20 }}>{MOOD_EMOJIS.find(([v]) => v === moodLog[0].value)?.[1]}</span>
              <div>
                <div>{moodLog[0].label}</div>
                <div className="text-xs font-normal text-slate-400">{fmtTime(moodLog[0].datetime)}</div>
              </div>
            </div>
          )}
        </div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          {MOOD_EMOJIS.map(([val, emoji, label]) => (
            <button key={val} onClick={() => handleMoodLog(val)}
              style={{
                display: "flex", flexDirection: "column", alignItems: "center", gap: 4,
                flex: 1, minWidth: 64, paddingTop: 12, paddingBottom: 10, borderRadius: 18,
                border: `2px solid ${currentMood === val ? "var(--accent)" : "var(--border)"}`,
                background: currentMood === val ? "var(--accent-glow)" : "var(--glass-thin)",
                cursor: "pointer", transition: "all 0.15s", fontSize: 28, lineHeight: 1,
                boxShadow: currentMood === val ? "0 4px 14px var(--accent-glow)" : "var(--specular)",
              }}>
              {emoji}
              <span style={{ fontSize: 11, fontWeight: 700, color: currentMood === val ? "var(--accent)" : "var(--ink2)" }}>{label}</span>
            </button>
          ))}
        </div>
      </div>

      {/* KPIs */}
      <Kpi title="Среднее за всё время" value={`${overallAvg}/9`} sub={`${total} записей`} icon={TrendingUp} tone="green" />
      <Kpi title="Среднее сегодня" value={todayAvg > 0 ? `${todayAvg}/9` : "—"} sub={`${todayEntries.length} записей`} icon={Smile} />
      <Kpi title="Топ настроение" value={`${bestEmoji[1]} ${bestEmoji[2]}`} sub={`${moodLog.filter((e) => e.value === bestEmoji[0]).length} раз`} icon={Sparkles} tone="green" />

      {/* Main trend chart */}
      <Card className="xl:col-span-2">
        <div className="mb-4 flex items-center justify-between">
          <SectionTitle title={`Динамика — ${PERIOD_LABELS[viewPeriod]}`} />
          <div className="flex rounded-full border border-[var(--border)] bg-[var(--glass-heavy)] p-1 gap-0.5 shadow-soft">
            {(Object.keys(PERIOD_LABELS) as (keyof typeof PERIOD_LABELS)[]).map((p) => (
              <button key={p} onClick={() => setViewPeriod(p)}
                style={{ color: viewPeriod === p ? "" : "var(--ink2)" }}
                className={`rounded-full px-3 py-1 text-xs font-bold transition ${viewPeriod === p ? "bg-accent text-white shadow" : ""}`}>
                {PERIOD_LABELS[p]}
              </button>
            ))}
          </div>
        </div>
        <ChartWrap>
          <LineChart data={chartData}>
            <CartesianGrid vertical={false} stroke="var(--grid-line)" />
            <XAxis dataKey="label" tickLine={false} axisLine={false} tick={{ fill: "var(--ink2)", fontSize: 11 }} />
            <YAxis hide domain={[0, 10]} />
            <Tooltip formatter={(v: unknown) => [`${v}/9`, "Настроение"]} />
            <Line type="monotone" dataKey="value" stroke="var(--accent)" strokeWidth={3} dot={{ r: 4, fill: "var(--accent)" }} connectNulls />
          </LineChart>
        </ChartWrap>
      </Card>

      {/* Good/Neutral/Bad pie */}
      <Card>
        <SectionTitle title="Хорошее / Нейтр. / Плохое" />
        <ChartWrap>
          <PieChart>
            <Pie data={pieData} dataKey="value" nameKey="name" innerRadius={60} outerRadius={95} paddingAngle={3}>
              {pieData.map((d, i) => <Cell key={i} fill={d.fill} />)}
            </Pie>
            <Tooltip formatter={(v: unknown, name: unknown) => [`${Math.round((Number(v) / total) * 100)}% (${v} раз)`, name as string]} />
          </PieChart>
        </ChartWrap>
        <div className="flex justify-around mt-2">
          {pieData.map((d) => (
            <div key={d.name} className="text-center">
              <div className="text-lg font-bold" style={{ color: d.fill }}>{Math.round((d.value / total) * 100)}%</div>
              <div className="text-xs text-slate-500">{d.name}</div>
            </div>
          ))}
        </div>
      </Card>

      {/* Frequency bar */}
      <Card className="xl:col-span-2">
        <SectionTitle title="Частота по типам" />
        <ChartWrap small>
          <BarChart data={freqData} layout="vertical">
            <XAxis type="number" hide />
            <YAxis type="category" dataKey="name" tickLine={false} axisLine={false} tick={{ fill: "var(--ink2)", fontSize: 13 }} width={110} />
            <Tooltip formatter={(v: unknown) => [`${v} раз`, "Количество"]} />
            <Bar dataKey="count" radius={[0, 8, 8, 0]}>
              {freqData.map((d, i) => <Cell key={i} fill={MOOD_COLOR(d.value)} />)}
            </Bar>
          </BarChart>
        </ChartWrap>
      </Card>

      {/* Hourly today if has data */}
      {todayEntries.length > 0 && (
        <Card>
          <SectionTitle title="Сегодня по часам" />
          <ChartWrap small>
            <BarChart data={hourlyData}>
              <XAxis dataKey="hour" tickLine={false} axisLine={false} tick={{ fill: "var(--ink2)", fontSize: 10 }} />
              <YAxis hide domain={[0, 10]} />
              <Tooltip formatter={(v: unknown) => [`${v}/9`, "Настроение"]} />
              <Bar dataKey="value" radius={[6, 6, 0, 0]}>
                {hourlyData.map((d, i) => <Cell key={i} fill={MOOD_COLOR(d.value ?? 0)} />)}
              </Bar>
            </BarChart>
          </ChartWrap>
        </Card>
      )}

      {/* History log */}
      <Card className="xl:col-span-3">
        <SectionTitle title="История записей" sub={`Всего ${total}`} />
        <div className="overflow-x-auto">
          <table className="table">
            <thead>
              <tr><th>Дата</th><th>Время</th><th>Настроение</th><th>Уровень</th><th>Категория</th></tr>
            </thead>
            <tbody>
              {moodLog.slice(0, 30).map((e) => (
                <tr key={e.id}>
                  <td>{new Date(e.datetime).toLocaleDateString("ru-RU")}</td>
                  <td>{fmtTime(e.datetime)}</td>
                  <td>
                    <span style={{ fontSize: 18, marginRight: 6 }}>{MOOD_EMOJIS.find(([v]) => v === e.value)?.[1]}</span>
                    <span className="font-semibold">{e.label}</span>
                  </td>
                  <td>
                    <div className="flex items-center gap-2">
                      <div style={{ width: `${(e.value / 9) * 80}px`, height: 6, borderRadius: 999, background: MOOD_COLOR(e.value) }} />
                      <span className="text-xs text-slate-400">{e.value}/9</span>
                    </div>
                  </td>
                  <td><span className="badge" style={{ background: MOOD_BG(e.value), color: MOOD_COLOR(e.value) }}>{MOOD_CAT(e.value)}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
    </PageGrid>
  );
}

function SettingsPage({ data, setData }: { data: AppData; setData: React.Dispatch<React.SetStateAction<AppData>> }) {
  const update = (patch: Partial<SettingsData>) => setData((p) => ({ ...p, settings: { ...p.settings, ...patch } }));
  return <PageGrid>
    <Card><SectionTitle title="Profile mock" /><div className="flex items-center gap-4"><div className="grid h-14 w-14 place-items-center rounded-full bg-accent text-xl font-semibold text-white">M</div><div><p className="font-semibold">My Life OS</p><p className="text-sm text-slate-500">Local demo workspace</p></div></div></Card>
    <Card className="xl:col-span-2">
      <SectionTitle title="Goal targets" />
      <div className="grid gap-4 md:grid-cols-2">
        {(["caloriesGoal", "proteinGoal", "fatGoal", "carbsGoal", "waterGoal", "sleepGoal", "monthlyBudget"] as const).map((key) => (
          <label key={key} className="field">
            <span>{GOAL_LABELS[key] || key}</span>
            <input type="number" value={data.settings[key] ?? ""} onChange={(e) => update({ [key]: e.target.value === "" ? "" : Number(e.target.value) } as any)} placeholder="Введите значение..." />
          </label>
        ))}
      </div>
    </Card>
    <Card><SectionTitle title="Preferences" /><label className="flex items-center justify-between rounded-xl bg-slate-50 p-3 text-sm"><span>Compact mode UI</span><input type="checkbox" checked={data.settings.compactMode} onChange={(e) => update({ compactMode: e.target.checked })} /></label><button className="mt-4 w-full rounded-xl bg-rose-50 px-4 py-3 text-sm font-semibold text-rose-600" onClick={() => { localStorage.removeItem(STORAGE_KEY); setData(mockData); }}>Reset demo data</button></Card>
  </PageGrid>;
}

function PageGrid({ children }: { children: React.ReactNode }) {
  return <div className="grid gap-4 grid-cols-1 xl:grid-cols-3">{children}</div>;
}

function TaskList({ tasks, setData }: { tasks: Task[]; setData: React.Dispatch<React.SetStateAction<AppData>> }) {
  return (
    <div className="max-h-[340px] overflow-y-auto pr-1.5 custom-scrollbar">
      {tasks.length === 0 ? (
        <p className="text-sm text-slate-400 dark:text-slate-500 py-4 text-center">Нет задач</p>
      ) : (
        <div className="space-y-2">
          {tasks.map((t) => (
            <button key={t.id} onClick={() => setData((p) => ({ ...p, tasks: p.tasks.map((x) => x.id === t.id ? { ...x, status: x.status === "done" ? "today" : "done" } : x) }))} className="task-row">
              <span className={`check ${t.status === "done" ? "checked" : ""}`} />
              <span className="min-w-0 flex-1 text-left"><strong>{t.title}</strong><small>{t.due}</small></span>
              <em className={`priority ${t.priority}`}>{t.priority}</em>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function HabitLine({ habit, setData, grid = false }: { habit: Habit; setData: React.Dispatch<React.SetStateAction<AppData>>; grid?: boolean }) {
  const done = habit.doneDates.includes(iso(0));
  return <div><div className="mb-2 flex items-center justify-between gap-3"><button className="flex items-center gap-2 text-sm font-medium" onClick={() => setData((p) => ({ ...p, habits: p.habits.map((h) => h.id === habit.id ? { ...h, doneDates: done ? h.doneDates.filter((d) => d !== iso(0)) : [...h.doneDates, iso(0)], streak: done ? Math.max(0, h.streak - 1) : h.streak + 1 } : h) }))}><span className={`check ${done ? "checked" : ""}`} />{habit.title}</button><span className="text-xs text-slate-400">{habit.streak} streak</span></div>{grid ? <div className="grid grid-cols-7 gap-1">{Array.from({ length: 7 }, (_, i) => iso(i - 6)).map((d) => <span key={d} className={`h-7 rounded-lg ${habit.doneDates.includes(d) ? "bg-accent" : "bg-slate-100"}`} />)}</div> : <Progress value={habit.doneDates.length / habit.target * 100} />}</div>;
}

function TransactionsTable({ rows, fmtC, setData, expenseCategories, incomeCategories }: {
  rows: Transaction[];
  fmtC?: (n: number) => string;
  setData?: React.Dispatch<React.SetStateAction<AppData>>;
  expenseCategories?: string[];
  incomeCategories?: string[];
}) {
  const fmt = fmtC ?? ((n: number) => money.format(n));
  const [editing, setEditing] = useState<Transaction | null>(null);
  const sorted = [...rows].sort((a, b) => {
    const da = `${a.date}T${a.time ?? "00:00"}`;
    const db = `${b.date}T${b.time ?? "00:00"}`;
    return db.localeCompare(da);
  });

  const handleDelete = (txId: string) => {
    if (!setData) return;
    setData((p) => ({ ...p, transactions: p.transactions.filter((t) => t.id !== txId) }));
  };

  return (
    <>
      <div className="overflow-x-auto">
        <table className="table">
          <thead>
            <tr>
              <th>Дата</th>
              <th>Время</th>
              <th>Описание</th>
              <th>Категория</th>
              <th>Тип</th>
              <th>Сумма</th>
              {setData && <th></th>}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.id}>
                <td className="text-slate-500 text-xs whitespace-nowrap">{r.date}</td>
                <td className="text-slate-400 text-xs whitespace-nowrap font-mono">{r.time ?? "—"}</td>
                <td>
                  <div className="font-medium">{r.title}</div>
                  {r.personName && <div className="text-xs text-slate-400">{r.personName}</div>}
                </td>
                <td><span className={r.type === "income" ? "income-tag" : "expense-tag"}>{r.category}</span></td>
                <td><span className={`badge ${r.type === "income" ? "income-badge" : "expense-badge"}`}>{r.type === "income" ? "Доход" : "Расход"}</span></td>
                <td className={`font-semibold whitespace-nowrap ${r.type === "income" ? "text-income" : "text-expense"}`}>
                  {r.type === "income" ? "+" : "−"}{fmt(r.amount)}
                </td>
                {setData && (
                  <td>
                    <div className="flex gap-1">
                      <button
                        onClick={() => setEditing(r)}
                        className="rounded-lg px-2 py-1 text-xs font-semibold text-slate-500 hover:bg-slate-100 dark:hover:bg-slate-800 transition"
                        title="Редактировать"
                      >
                        ✏️
                      </button>
                      <button
                        onClick={() => handleDelete(r.id)}
                        className="rounded-lg px-2 py-1 text-xs font-semibold text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/30 transition"
                        title="Удалить"
                      >
                        🗑
                      </button>
                    </div>
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {editing && setData && (
        <Modal title="Редактировать транзакцию" close={() => setEditing(null)}>
          <TransactionEditForm
            tx={editing}
            categories={editing.type === "expense" ? (expenseCategories ?? []) : (incomeCategories ?? [])}
            setData={setData}
            close={() => setEditing(null)}
          />
        </Modal>
      )}
    </>
  );
}

function TransactionEditForm({ tx, categories, setData, close }: {
  tx: Transaction;
  categories: string[];
  setData: React.Dispatch<React.SetStateAction<AppData>>;
  close: () => void;
}) {
  const [title, setTitle] = useState(tx.title);
  const [amount, setAmount] = useState<number | "">(tx.amount);
  const [category, setCategory] = useState(tx.category);
  const [personName, setPersonName] = useState(tx.personName ?? "");
  const [date, setDate] = useState(tx.date);

  const save = (e: React.FormEvent) => {
    e.preventDefault();
    setData((p) => ({
      ...p,
      transactions: p.transactions.map((t) =>
        t.id === tx.id ? { ...t, title, amount: Number(amount) || 0, category, personName: personName || undefined, date } : t
      ),
    }));
    close();
  };

  return (
    <form className="form-grid" onSubmit={save}>
      {/* Read-only info */}
      <div className="md:col-span-2 flex gap-4 rounded-2xl bg-[var(--glass-thin)] px-4 py-3 text-sm text-[var(--ink2)] border border-[var(--border)]">
        <span><strong>Тип:</strong> {tx.type === "income" ? "Доход" : "Расход"}</span>
        <span><strong>Создано:</strong> {tx.date} в {tx.time ?? "—"}</span>
      </div>

      <label className="field">
        <span>Название</span>
        <input value={title} onChange={(e) => setTitle(e.target.value)} required />
      </label>

      <label className="field">
        <span>Сумма (₴)</span>
        <input type="number" value={amount} onChange={(e) => setAmount(e.target.value === "" ? "" : Number(e.target.value))} required min={0} placeholder="Введите сумму..." />
      </label>

      <label className="field">
        <span>Категория</span>
        <select value={category} onChange={(e) => setCategory(e.target.value)}>
          {categories.map((c) => <option key={c}>{c}</option>)}
        </select>
      </label>

      <label className="field">
        <span>Дата</span>
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
      </label>

      {tx.type === "income" && (
        <label className="field md:col-span-2">
          <span>Имя клиента</span>
          <input value={personName} onChange={(e) => setPersonName(e.target.value)} />
        </label>
      )}

      <button className="primary-btn justify-center md:col-span-2" type="submit">
        <Plus size={16} />Сохранить изменения
      </button>
    </form>
  );
}

function nowTime() { const d = new Date(); return `${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}`; }

function ExpenseForm({ add, categories, after }: { add: any; categories: string[]; after?: () => void }) {
  const [f, bind] = useFormState({ title: "", amount: "" as any, category: categories[0] ?? "Прочее", date: iso(0) });
  return (
    <Form onSubmit={() => {
      add("transactions", { id: id(), ...f, amount: Number(f.amount) || 0, time: nowTime(), type: "expense", currency: "UAH" });
      after?.();
    }}>
      <Input label="На что потратил" {...bind("title")} />
      <Input label="Сумма (₴)" type="number" placeholder="Введите сумму..." {...bind("amount")} />
      <Select label="Категория" {...bind("category")} options={categories} />
      <Input label="Дата" type="date" {...bind("date")} />
      <div className="field md:col-span-2">
        <span>Время</span>
        <div className="min-h-10 flex items-center px-3 rounded-xl border border-[var(--border)] bg-[var(--glass-thin)] text-sm text-[var(--ink2)]">
          Подставится автоматически ({nowTime()})
        </div>
      </div>
    </Form>
  );
}

function IncomeForm({ add, categories, after }: { add: any; categories: string[]; after?: () => void }) {
  const [f, bind] = useFormState({ title: "", personName: "", amount: "" as any, category: categories[0] ?? "Фриланс", date: iso(0) });
  return (
    <Form onSubmit={() => {
      add("transactions", {
        id: id(),
        title: f.title || `${f.category} — ${f.personName}`,
        personName: f.personName,
        amount: Number(f.amount) || 0,
        category: f.category,
        type: "income",
        currency: "UAH",
        date: f.date,
        time: nowTime(),
      });
      after?.();
    }}>
      <Input label="Имя клиента / плательщика" {...bind("personName")} />
      <Input label="Сумма (₴)" type="number" placeholder="Введите сумму..." {...bind("amount")} />
      <Select label="Категория работы" {...bind("category")} options={categories} />
      <Input label="Название (необязательно)" {...bind("title")} />
      <Input label="Дата" type="date" {...bind("date")} />
      <div className="field md:col-span-2">
        <span>Время</span>
        <div className="min-h-10 flex items-center px-3 rounded-xl border border-[var(--border)] bg-[var(--glass-thin)] text-sm text-[var(--ink2)]">
          Подставится автоматически ({nowTime()})
        </div>
      </div>
    </Form>
  );
}

function SimpleList({ items }: { items: string[][] }) {
  return <div className="space-y-2">{items.map((it, i) => <div className="list-row" key={`${it[0]}-${i}`}><div><p className="font-medium">{it[0]}</p><p className="text-sm text-slate-500">{it[1]}</p></div><span>{it[2]}</span></div>)}</div>;
}

function QuickAdd({ mode, setMode, add, close }: { mode: string; setMode: (m: string | null) => void; add: any; close: () => void }) {
  const choices = [["expense", "Расход"], ["income", "Доход"], ["meal", "Еда"], ["health", "Вес/здоровье"], ["workout", "Тренировка"], ["task", "Задача"], ["habit", "Привычка"], ["journal", "Запись"], ["event", "Событие"]];
  return <Modal title={mode === "menu" ? "Быстро добавить" : choices.find((c) => c[0] === mode)?.[1] ?? "Добавить"} close={close}>
    {mode === "menu" ? <div className="grid gap-2 sm:grid-cols-2">{choices.map(([k, label]) => <button key={k} className="quick-choice" onClick={() => setMode(k)}><Plus size={17} />{label}</button>)}</div> : <DynamicForm mode={mode} add={add} close={close} />}
  </Modal>;
}

function DynamicForm({ mode, add, close }: { mode: string; add: any; close: () => void }) {
  if (mode === "expense" || mode === "income") return <TransactionForm add={add} fixedType={mode} after={close} />;
  if (mode === "meal") return <MealForm add={add} after={close} />;
  if (mode === "health") return <HealthForm add={add} after={close} />;
  if (mode === "workout") return <WorkoutForm add={add} after={close} />;
  if (mode === "task") return <TaskForm add={add} after={close} />;
  if (mode === "habit") return <HabitForm add={add} after={close} />;
  if (mode === "journal") return <JournalForm add={add} after={close} />;
  return <EventForm add={add} after={close} />;
}

function useFormState<T>(initial: T) {
  const [form, setForm] = useState(initial);
  const bind = (key: keyof T) => ({
    value: form[key] as string | number,
    onChange: (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>) =>
      setForm((f) => ({
        ...f,
        [key]: e.target.type === "number"
          ? e.target.value === "" ? "" : Number(e.target.value)
          : e.target.value
      }))
  });
  return [form, bind, setForm] as const;
}

function TransactionForm({ add, fixedType, after }: { add: any; fixedType?: TxType; after?: () => void }) {
  const [f, bind] = useFormState({ title: "", amount: "" as any, category: "Прочее", type: fixedType ?? "expense", date: iso(0) });
  return <Form onSubmit={() => { add("transactions", { id: id(), ...f, amount: Number(f.amount) || 0, type: fixedType ?? f.type, currency: "UAH" }); after?.(); }}><Input label="Название" {...bind("title")} /><Input label="Сумма (₴)" type="number" placeholder="Введите сумму..." {...bind("amount")} /><Input label="Категория" {...bind("category")} /><Input label="Дата" type="date" {...bind("date")} /></Form>;
}

function FoodForm({ setData, after }: { setData: React.Dispatch<React.SetStateAction<AppData>>; after?: () => void }) {
  const [f, bind] = useFormState({ name: "", brand: "", cal100: "" as any, pro100: "" as any, fat100: "" as any, carb100: "" as any });
  return (
    <Form onSubmit={() => {
      const newFood = { id: id(), name: f.name, brand: f.brand || undefined, cal100: Number(f.cal100) || 0, pro100: Number(f.pro100) || 0, fat100: Number(f.fat100) || 0, carb100: Number(f.carb100) || 0 };
      addFoodToSupabase(newFood).then((sbRow) => {
        setData((p) => ({ ...p, foods: [sbRow ? sbProductToFood(sbRow) : { id: newFood.id, name: newFood.name + (f.brand ? ` (${f.brand})` : ""), cal100: newFood.cal100, pro100: newFood.pro100, fat100: newFood.fat100, carb100: newFood.carb100 }, ...p.foods] }));
      });
      after?.();
    }}>
      <Input label="Название продукта" {...bind("name")} />
      <Input label="Торговая марка (необязательно)" {...bind("brand")} />
      <Input label="Калории на 100 г" type="number" placeholder="Введите калории..." {...bind("cal100")} />
      <Input label="Белки на 100 г" type="number" placeholder="Введите белки..." {...bind("pro100")} />
      <Input label="Жиры на 100 г" type="number" placeholder="Введите жиры..." {...bind("fat100")} />
      <Input label="Углеводы на 100 г" type="number" placeholder="Введите углеводы..." {...bind("carb100")} />
    </Form>
  );
}

type DraftItem = { id: string; name: string; foodId?: string; weight: number; calories: number; protein: number; fat: number; carbs: number };

function MealFormSmart({ foods, add, after }: { foods: FoodItem[]; add: any; after?: () => void }) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<FoodItem | null>(null);
  const [weight, setWeight] = useState<number | "">(100);
  const [mealType, setMealType] = useState("Завтрак");
  const [date, setDate] = useState(iso(0));
  const [showSuggestions, setShowSuggestions] = useState(false);
  const [items, setItems] = useState<DraftItem[]>([]);

  const suggestions = query.length > 0
    ? foods.filter((f) => f.name.toLowerCase().includes(query.toLowerCase())).slice(0, 6)
    : [];

  const weightNum = Number(weight) || 0;
  const calc = selected ? {
    calories: Math.round(selected.cal100 * weightNum / 100),
    protein: Math.round(selected.pro100 * weightNum / 100 * 10) / 10,
    fat: Math.round(selected.fat100 * weightNum / 100 * 10) / 10,
    carbs: Math.round(selected.carb100 * weightNum / 100 * 10) / 10,
  } : null;

  const handleSelect = (food: FoodItem) => {
    setSelected(food);
    setQuery(food.name);
    setShowSuggestions(false);
  };

  // Текущий продукт как черновик (если выбран и введён вес)
  const currentDraft = (): DraftItem | null => {
    const name = selected ? selected.name : query.trim();
    if (!name) return null;
    const macros = calc ?? { calories: 0, protein: 0, fat: 0, carbs: 0 };
    return { id: id(), name, foodId: selected?.id, weight: Number(weight) || 100, ...macros };
  };

  const addAnother = () => {
    const d = currentDraft();
    if (!d) return;
    setItems((prev) => [...prev, d]);
    setQuery(""); setSelected(null); setWeight(100); setShowSuggestions(false);
  };

  const removeItem = (rid: string) => setItems((prev) => prev.filter((i) => i.id !== rid));

  // Все продукты приёма = добавленные + текущий незавершённый (если есть)
  const allItems = (): DraftItem[] => {
    const d = currentDraft();
    return d ? [...items, d] : items;
  };

  const total = allItems().reduce((a, i) => ({
    calories: a.calories + i.calories,
    protein: Math.round((a.protein + i.protein) * 10) / 10,
    fat: Math.round((a.fat + i.fat) * 10) / 10,
    carbs: Math.round((a.carbs + i.carbs) * 10) / 10,
  }), { calories: 0, protein: 0, fat: 0, carbs: 0 });

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const list = allItems();
    if (list.length === 0) return;
    for (const it of list) {
      const meal = { date, name: it.name, mealType, weight: it.weight, calories: it.calories, protein: it.protein, fat: it.fat, carbs: it.carbs };
      addMealToSupabase(meal).then((sbRow) => {
        if (sbRow) add("meals", sbFoodLogToMeal(sbRow));
        else add("meals", { id: it.id, ...meal });
      });
    }
    after?.();
  };

  return (
    <form className="form-grid" onSubmit={handleSubmit}>
      {/* Уже добавленные продукты */}
      {items.length > 0 && (
        <div className="md:col-span-2 space-y-2">
          {items.map((it) => (
            <div key={it.id} className="flex items-center justify-between rounded-xl bg-orange-50 dark:bg-slate-800 px-3 py-2 text-sm">
              <span><strong>{it.name}</strong> · {it.weight} г</span>
              <span className="flex items-center gap-3">
                <span className="text-accent font-semibold">{it.calories} ккал</span>
                <button type="button" onClick={() => removeItem(it.id)} className="text-rose-400 hover:text-rose-600" title="Убрать">✕</button>
              </span>
            </div>
          ))}
        </div>
      )}

      {/* Autocomplete */}
      <label className="field md:col-span-2" style={{ position: "relative" }}>
        <span>Продукт{items.length > 0 ? " (ещё)" : ""}</span>
        <input
          value={query}
          onChange={(e) => { setQuery(e.target.value); setSelected(null); setShowSuggestions(true); }}
          onFocus={() => setShowSuggestions(true)}
          onBlur={() => setTimeout(() => setShowSuggestions(false), 150)}
          placeholder="Начни вводить название или марку..."
        />
        {showSuggestions && suggestions.length > 0 && (
          <div style={{
            position: "absolute", top: "100%", left: 0, right: 0, zIndex: 50,
            background: "var(--card-bg-solid)", border: "1px solid var(--border)", borderRadius: 14,
            backdropFilter: "var(--blur-md)", WebkitBackdropFilter: "var(--blur-md)",
            boxShadow: "var(--shadow-lift)", marginTop: 4, overflow: "hidden",
          }}>
            {suggestions.map((f) => (
              <button key={f.id} type="button" onMouseDown={() => handleSelect(f)}
                style={{ display: "block", width: "100%", padding: "10px 14px", textAlign: "left", fontSize: 13, borderBottom: "1px solid var(--border-thin)", color: "var(--ink)" }}
                className="hover:bg-orange-50 dark:hover:bg-slate-800">
                <strong>{f.name}</strong>
                <span style={{ marginLeft: 8, color: "var(--ink2)", fontSize: 11 }}>{f.cal100} ккал · Б{f.pro100} Ж{f.fat100} У{f.carb100}</span>
              </button>
            ))}
          </div>
        )}
      </label>

      <label className="field">
        <span>Вес порции (г)</span>
        <input type="number" value={weight} onChange={(e) => setWeight(e.target.value === "" ? "" : Number(e.target.value))} min={1} placeholder="Введите вес..." />
      </label>

      <label className="field">
        <span>Приём пищи</span>
        <select value={mealType} onChange={(e) => setMealType(e.target.value)}>
          {["Завтрак","Обед","Ужин","Перекус"].map((o) => <option key={o}>{o}</option>)}
        </select>
      </label>

      <label className="field md:col-span-2">
        <span>Дата</span>
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
      </label>

      {/* Кнопка «Добавить ещё продукт» */}
      <button type="button" onClick={addAnother} disabled={!currentDraft()}
        className="md:col-span-2 flex items-center justify-center gap-2 rounded-xl border border-dashed border-accent/50 py-2.5 text-sm font-semibold text-accent disabled:opacity-40 hover:bg-orange-50 dark:hover:bg-slate-800 transition">
        <Plus size={16} />Добавить ещё продукт
      </button>

      {/* Общий итог по приёму */}
      {allItems().length > 0 && (
        <div className="md:col-span-2 grid grid-cols-4 gap-3 rounded-2xl bg-orange-50 dark:bg-slate-800 p-4">
          {[["Ккал", total.calories], ["Белки", `${total.protein}г`], ["Жиры", `${total.fat}г`], ["Углеводы", `${total.carbs}г`]].map(([l, v]) => (
            <div key={l as string} className="text-center">
              <div className="text-xs font-semibold uppercase text-slate-400">{l as string}</div>
              <div className="mt-1 text-lg font-bold text-accent">{v as string | number}</div>
            </div>
          ))}
        </div>
      )}

      <button className="primary-btn justify-center md:col-span-2" type="submit" disabled={allItems().length === 0}>
        <Plus size={17} />Сохранить приём ({allItems().length})
      </button>
    </form>
  );
}

function MealForm({ add, after }: { add: any; after?: () => void }) {
  const [f, bind] = useFormState({ name: "", calories: "" as any, protein: "" as any, fat: "" as any, carbs: "" as any, weight: "" as any, mealType: "Завтрак", date: iso(0) });
  return (
    <Form onSubmit={() => {
      add("meals", {
        id: id(),
        ...f,
        calories: Number(f.calories) || 0,
        protein: Number(f.protein) || 0,
        fat: Number(f.fat) || 0,
        carbs: Number(f.carbs) || 0,
        weight: Number(f.weight) || 0
      });
      after?.();
    }}>
      <Input label="Название" {...bind("name")} />
      <Input label="Вес (г)" type="number" placeholder="Введите вес..." {...bind("weight")} />
      <Input label="Калории" type="number" placeholder="Введите калории..." {...bind("calories")} />
      <Input label="Белки" type="number" placeholder="Введите белки..." {...bind("protein")} />
      <Input label="Жиры" type="number" placeholder="Введите жиры..." {...bind("fat")} />
      <Input label="Углеводы" type="number" placeholder="Введите углеводы..." {...bind("carbs")} />
      <Select label="Приём пищи" {...bind("mealType")} options={["Завтрак","Обед","Ужин","Перекус"]} />
    </Form>
  );
}
function HealthForm({ add, after }: { add: any; after?: () => void }) {
  const [f, bind] = useFormState({ sleep: "" as any, water: "" as any, mood: "" as any, date: iso(0) });
  return (
    <Form onSubmit={() => {
      add("health", {
        id: id(),
        ...f,
        sleep: Number(f.sleep) || 0,
        water: Number(f.water) || 0,
        mood: Number(f.mood) || 0
      });
      after?.();
    }}>
      <Input label="Сон (часы)" type="number" placeholder="Введите часы сна..." {...bind("sleep")} />
      <Input label="Вода (литры)" type="number" placeholder="Введите литры воды..." {...bind("water")} />
      <Input label="Настроение (1-10)" type="number" placeholder="Оцените настроение..." {...bind("mood")} />
      <Input label="Дата" type="date" {...bind("date")} />
    </Form>
  );
}

function BodyLogForm({ setData, after }: { setData: React.Dispatch<React.SetStateAction<AppData>>; after?: () => void }) {
  const [f, bind] = useFormState({
    date: iso(0),
    weight: "" as any,
    bmi: "" as any,
    fatPct: "" as any,
    musclePct: "" as any,
    waterPct: "" as any,
    boneMass: "" as any,
    metabolism: "" as any,
    proteinPct: "" as any,
    bodyAge: "" as any,
    visceralFat: "" as any,
    fatKg: "" as any,
    leanMass: "" as any,
    muscleKg: "" as any,
    proteinKg: "" as any
  });
  return (
    <Form onSubmit={() => {
      const newLog = {
        id: id(),
        date: f.date,
        weight: Number(f.weight) || 0,
        bmi: Number(f.bmi) || 0,
        fatPct: Number(f.fatPct) || 0,
        musclePct: Number(f.musclePct) || 0,
        waterPct: Number(f.waterPct) || 0,
        boneMass: Number(f.boneMass) || 0,
        metabolism: Number(f.metabolism) || 0,
        proteinPct: Number(f.proteinPct) || 0,
        bodyAge: Number(f.bodyAge) || 0,
        visceralFat: Number(f.visceralFat) || 0,
        fatKg: Number(f.fatKg) || 0,
        leanMass: Number(f.leanMass) || 0,
        muscleKg: Number(f.muscleKg) || 0,
        proteinKg: Number(f.proteinKg) || 0,
      };
      addBodyLogToSupabase(newLog).then((sbRow) => {
        setData((p) => ({
          ...p,
          bodyLogs: [sbRow ? sbBodyToBodyLog(sbRow) : newLog, ...(p.bodyLogs ?? [])]
        }));
      });
      after?.();
    }}>
      <Input label="Дата" type="date" {...bind("date")} />
      <Input label="Вес (кг)" type="number" placeholder="Введите вес..." {...bind("weight")} />
      <Input label="ИМТ" type="number" placeholder="Введите ИМТ..." {...bind("bmi")} />
      <Input label="Жир %" type="number" placeholder="Введите % жира..." {...bind("fatPct")} />
      <Input label="Мышцы %" type="number" placeholder="Введите % мышц..." {...bind("musclePct")} />
      <Input label="Вода %" type="number" placeholder="Введите % воды..." {...bind("waterPct")} />
      <Input label="Кости (кг)" type="number" placeholder="Введите кости (кг)..." {...bind("boneMass")} />
      <Input label="Метаболизм (ккал)" type="number" placeholder="Введите метаболизм..." {...bind("metabolism")} />
      <Input label="Белок %" type="number" placeholder="Введите % белка..." {...bind("proteinPct")} />
      <Input label="Возраст тела" type="number" placeholder="Введите возраст..." {...bind("bodyAge")} />
      <Input label="Висц. жир" type="number" placeholder="Введите висцеральный жир..." {...bind("visceralFat")} />
      <Input label="Жир (кг)" type="number" placeholder="Введите вес жира..." {...bind("fatKg")} />
      <Input label="Без жира (кг)" type="number" placeholder="Введите вес без жира..." {...bind("leanMass")} />
      <Input label="Мышцы (кг)" type="number" placeholder="Введите вес мышц..." {...bind("muscleKg")} />
      <Input label="Протеин (кг)" type="number" placeholder="Введите вес белка..." {...bind("proteinKg")} />
    </Form>
  );
}
function WorkoutForm({ add, after }: { add: any; after?: () => void }) {
  const [f, bind] = useFormState({ type: "Strength", duration: "" as any, calories: "" as any, steps: "" as any, date: iso(0) });
  return (
    <Form onSubmit={() => {
      add("workouts", {
        id: id(),
        ...f,
        duration: Number(f.duration) || 0,
        calories: Number(f.calories) || 0,
        steps: Number(f.steps) || 0
      });
      after?.();
    }}>
      <Input label="Тип" {...bind("type")} />
      <Input label="Duration" type="number" placeholder="Введите длительность..." {...bind("duration")} />
      <Input label="Calories" type="number" placeholder="Введите калории..." {...bind("calories")} />
      <Input label="Steps" type="number" placeholder="Введите шаги..." {...bind("steps")} />
      <Input label="Дата" type="date" {...bind("date")} />
    </Form>
  );
}
function TaskForm({ add, after }: { add: any; after?: () => void }) {
  const [f, bind] = useFormState({ title: "", status: "today", priority: "medium", due: iso(0) });
  return <Form onSubmit={() => { add("tasks", { id: id(), ...f }); after?.(); }}><Input label="Задача" {...bind("title")} /><Select label="Статус" {...bind("status")} options={["today", "upcoming", "done"]} /><Select label="Priority" {...bind("priority")} options={["low", "medium", "high"]} /><Input label="Due" type="date" {...bind("due")} /></Form>;
}
function HabitForm({ add, after }: { add: any; after?: () => void }) {
  const [f, bind] = useFormState({ title: "", target: "" as any });
  return (
    <Form onSubmit={() => {
      add("habits", {
        id: id(),
        title: f.title,
        target: Number(f.target) || 7,
        streak: 0,
        doneDates: []
      });
      after?.();
    }}>
      <Input label="Привычка" {...bind("title")} />
      <Input label="Target days" type="number" placeholder="Введите цель..." {...bind("target")} />
    </Form>
  );
}
function GoalForm({ add, after }: { add: any; after?: () => void }) {
  const [f, bind] = useFormState({ title: "", progress: "" as any, targetDate: iso(30), status: "on track", linked: "Habit" });
  return (
    <Form onSubmit={() => {
      add("goals", {
        id: id(),
        ...f,
        progress: Number(f.progress) || 0
      });
      after?.();
    }}>
      <Input label="Цель" {...bind("title")} />
      <Input label="Progress" type="number" placeholder="Введите прогресс..." {...bind("progress")} />
      <Input label="Target date" type="date" {...bind("targetDate")} />
      <Select label="Status" {...bind("status")} options={["on track", "behind", "completed"]} />
      <Input label="Linked" {...bind("linked")} />
    </Form>
  );
}
function JournalForm({ add, after }: { add: any; after?: () => void }) {
  const [f, bind] = useFormState({ text: "", mood: "focus", date: iso(0) });
  return <Form onSubmit={() => { add("journal", { id: id(), ...f }); after?.(); }}><Select label="Mood" {...bind("mood")} options={["focus", "calm", "good", "low"]} /><TextArea label="Запись" {...bind("text")} /><Input label="Дата" type="date" {...bind("date")} /></Form>;
}
function EventForm({ add, after, defaultDate }: { add: any; after?: () => void; defaultDate?: string }) {
  const [f, bind] = useFormState({ title: "", type: "Work", time: "10:00", date: defaultDate ?? iso(0) });
  return <Form onSubmit={() => { add("events", { id: id(), ...f }); after?.(); }}><Input label="Событие" {...bind("title")} /><Input label="Время" type="time" {...bind("time")} /><Input label="Тип" {...bind("type")} /><Input label="Дата" type="date" {...bind("date")} /></Form>;
}

function Form({ children, onSubmit }: { children: React.ReactNode; onSubmit: () => void }) {
  return <form className="form-grid" onSubmit={(e: FormEvent) => { e.preventDefault(); onSubmit(); }}>{children}<button className="primary-btn justify-center" type="submit"><Plus size={17} />Сохранить</button></form>;
}
function Input({ label, ...props }: React.InputHTMLAttributes<HTMLInputElement> & { label: string }) {
  return <label className="field"><span>{label}</span><input required {...props} /></label>;
}
function Select({ label, options, ...props }: React.SelectHTMLAttributes<HTMLSelectElement> & { label: string; options: string[] }) {
  return <label className="field"><span>{label}</span><select {...props}>{options.map((o) => <option key={o}>{o}</option>)}</select></label>;
}
function TextArea({ label, ...props }: React.TextareaHTMLAttributes<HTMLTextAreaElement> & { label: string }) {
  return <label className="field md:col-span-2"><span>{label}</span><textarea required rows={4} {...props} /></label>;
}

function Modal({ title, children, close }: { title: string; children: React.ReactNode; close: () => void }) {
  return createPortal(
    <div className="modal-backdrop" onMouseDown={close}>
      <div className="modal" onMouseDown={(e) => e.stopPropagation()}>
        <div className="mb-5 flex items-center justify-between">
          <h3 className="text-lg font-semibold">{title}</h3>
          <button className="icon-btn" onClick={close}><X size={18} /></button>
        </div>
        {children}
      </div>
    </div>,
    document.body
  );
}
function CommandModal({ close, setPage }: { close: () => void; setPage: (p: Page) => void }) {
  return <Modal title="Command search" close={close}><div className="search-shell mb-4 flex"><Search size={17} /><input autoFocus placeholder="Найти раздел или действие..." className="w-full bg-transparent outline-none" /></div><div className="grid gap-2">{nav.slice(0, 8).map(([key, Icon, label]) => <button key={key} className="quick-choice" onClick={() => { setPage(key); close(); }}><Icon size={17} />{label}</button>)}</div></Modal>;
}
function EventModal({ event, close }: { event: EventItem; close: () => void }) {
  return <Modal title={event.title} close={close}><div className="space-y-3"><StatusRow good label="Дата" value={event.date} /><StatusRow label="Время" value={event.time} /><StatusRow good label="Тип" value={event.type} /></div></Modal>;
}

const GOAL_LABELS: Record<string, string> = {
  caloriesGoal: "Цель по калориям (ккал)",
  proteinGoal: "Цель по белкам (г)",
  fatGoal: "Цель по жирам (г)",
  carbsGoal: "Цель по углеводам (г)",
  waterGoal: "Цель по воде (л)",
  sleepGoal: "Цель по сну (ч)",
  monthlyBudget: "Ежемесячный бюджет (₴)",
};

function NutritionGoalsForm({ settings, setData, after }: { settings: SettingsData; setData: React.Dispatch<React.SetStateAction<AppData>>; after: () => void }) {
  const [calories, setCalories] = useState<number | "">(settings.caloriesGoal);
  const [protein, setProtein] = useState<number | "">(settings.proteinGoal ?? 150);
  const [fat, setFat] = useState<number | "">(settings.fatGoal ?? 70);
  const [carbs, setCarbs] = useState<number | "">(settings.carbsGoal ?? 250);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const g = {
      calories: Number(calories) || 0,
      protein: Number(protein) || 0,
      fat: Number(fat) || 0,
      carbs: Number(carbs) || 0,
    };
    setData((p) => ({
      ...p,
      settings: {
        ...p.settings,
        caloriesGoal: g.calories,
        proteinGoal: g.protein,
        fatGoal: g.fat,
        carbsGoal: g.carbs,
      },
    }));
    updateDailyGoalsInSupabase(g);
    after();
  };

  return (
    <form className="form-grid" onSubmit={handleSubmit}>
      <label className="field">
        <span>Калории (ккал)</span>
        <input type="number" value={calories} onChange={(e) => setCalories(e.target.value === "" ? "" : Number(e.target.value))} min={1} required placeholder="Введите калории..." />
      </label>
      <label className="field">
        <span>Белки (г)</span>
        <input type="number" value={protein} onChange={(e) => setProtein(e.target.value === "" ? "" : Number(e.target.value))} min={0} required placeholder="Введите белки..." />
      </label>
      <label className="field">
        <span>Жиры (г)</span>
        <input type="number" value={fat} onChange={(e) => setFat(e.target.value === "" ? "" : Number(e.target.value))} min={0} required placeholder="Введите жиры..." />
      </label>
      <label className="field">
        <span>Углеводы (г)</span>
        <input type="number" value={carbs} onChange={(e) => setCarbs(e.target.value === "" ? "" : Number(e.target.value))} min={0} required placeholder="Введите углеводы..." />
      </label>
      <div className="md:col-span-2 flex justify-end gap-2 mt-2">
        <button type="submit" className="primary-btn">Сохранить</button>
      </div>
    </form>
  );
}

function FoodEditForm({ food, setData, after }: { food: FoodItem; setData: React.Dispatch<React.SetStateAction<AppData>>; after: () => void }) {
  const [name, setName] = useState(food.name);
  const [cal100, setCal100] = useState<number | "">(food.cal100);
  const [pro100, setPro100] = useState<number | "">(food.pro100);
  const [fat100, setFat100] = useState<number | "">(food.fat100);
  const [carb100, setCarb100] = useState<number | "">(food.carb100);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const patch = { name, cal100: Number(cal100) || 0, pro100: Number(pro100) || 0, fat100: Number(fat100) || 0, carb100: Number(carb100) || 0 };
    setData((p) => ({
      ...p,
      foods: p.foods.map((f) => f.id === food.id ? { ...f, ...patch } : f),
    }));
    updateProductInSupabase(food.id, patch);
    after();
  };

  return (
    <form className="form-grid" onSubmit={handleSubmit}>
      <label className="field md:col-span-2">
        <span>Название продукта</span>
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} required />
      </label>
      <label className="field">
        <span>Калории на 100г</span>
        <input type="number" value={cal100} onChange={(e) => setCal100(e.target.value === "" ? "" : Number(e.target.value))} min={0} required placeholder="Введите калории..." />
      </label>
      <label className="field">
        <span>Белки на 100г (г)</span>
        <input type="number" value={pro100} onChange={(e) => setPro100(e.target.value === "" ? "" : Number(e.target.value))} min={0} step="0.1" required placeholder="Введите белки..." />
      </label>
      <label className="field">
        <span>Жиры на 100г (г)</span>
        <input type="number" value={fat100} onChange={(e) => setFat100(e.target.value === "" ? "" : Number(e.target.value))} min={0} step="0.1" required placeholder="Введите жиры..." />
      </label>
      <label className="field">
        <span>Углеводы на 100г (г)</span>
        <input type="number" value={carb100} onChange={(e) => setCarb100(e.target.value === "" ? "" : Number(e.target.value))} min={0} step="0.1" required placeholder="Введите углеводы..." />
      </label>
      <div className="md:col-span-2 flex justify-end gap-2 mt-2">
        <button type="submit" className="primary-btn">Сохранить</button>
      </div>
    </form>
  );
}

function MealEditForm({ meal, setData, after }: { meal: any; setData: React.Dispatch<React.SetStateAction<AppData>>; after: () => void }) {
  const [weight, setWeight] = useState<number | "">(meal.weight ?? 0);
  const [mealType, setMealType] = useState<string>(meal.mealType && meal.mealType !== "Без приёма" ? meal.mealType : "Завтрак");
  const [date, setDate] = useState<string>(meal.date);

  const oldW = Number(meal.weight) || 0;
  const newW = Number(weight) || 0;
  const ratio = oldW > 0 ? newW / oldW : 1;
  const recalc = {
    calories: Math.round((meal.calories ?? 0) * ratio),
    protein: Math.round((meal.protein ?? 0) * ratio * 10) / 10,
    fat: Math.round((meal.fat ?? 0) * ratio * 10) / 10,
    carbs: Math.round((meal.carbs ?? 0) * ratio * 10) / 10,
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const patch = { weight: newW, mealType, date, ...recalc };
    setData((p) => ({
      ...p,
      meals: p.meals.map((mm) => mm.id === meal.id ? { ...mm, ...patch } : mm),
    }));
    updateMealInSupabase(meal.id, patch);
    after();
  };

  return (
    <form className="form-grid" onSubmit={handleSubmit}>
      <div className="md:col-span-2 text-sm font-semibold">{meal.name}</div>
      <label className="field">
        <span>Вес ({meal.unit ?? "г"})</span>
        <input type="number" value={weight} onChange={(e) => setWeight(e.target.value === "" ? "" : Number(e.target.value))} min={1} required />
      </label>
      <label className="field">
        <span>Приём пищи</span>
        <select value={mealType} onChange={(e) => setMealType(e.target.value)}>
          {["Завтрак","Обед","Ужин","Перекус"].map((o) => <option key={o}>{o}</option>)}
        </select>
      </label>
      <label className="field md:col-span-2">
        <span>Дата</span>
        <input type="date" value={date} onChange={(e) => setDate(e.target.value)} />
      </label>
      <div className="md:col-span-2 grid grid-cols-4 gap-3 rounded-2xl bg-orange-50 dark:bg-slate-800 p-3">
        {[["Ккал", recalc.calories], ["Белки", `${recalc.protein}г`], ["Жиры", `${recalc.fat}г`], ["Углеводы", `${recalc.carbs}г`]].map(([l, v]) => (
          <div key={l as string} className="text-center">
            <div className="text-xs font-semibold uppercase text-slate-400">{l as string}</div>
            <div className="mt-1 text-base font-bold text-accent">{v as string | number}</div>
          </div>
        ))}
      </div>
      <div className="md:col-span-2 flex justify-end gap-2 mt-2">
        <button type="submit" className="primary-btn">Сохранить</button>
      </div>
    </form>
  );
}

export default App;
