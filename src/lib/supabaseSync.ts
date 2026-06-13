import { useEffect, useCallback } from 'react'
import { supabase } from './supabase'

const today = () => new Date().toISOString().slice(0, 10)

// ── Adapters: Supabase → App ──────────────────────────────────────

export function sbProductToFood(p: any) {
  return {
    id: p.id,
    name: p.name + (p.brand ? ` (${p.brand})` : ''),
    cal100: p.calories,
    pro100: p.protein,
    fat100: p.fat,
    carb100: p.carbs,
  }
}

export function sbFoodLogToMeal(r: any) {
  return {
    id: r.id,
    date: r.date,
    name: r.product_name,
    mealType: r.meal_number ? `Приём ${r.meal_number}` : 'Приём',
    calories: Math.round(r.calories ?? 0),
    protein: Math.round(r.protein ?? 0),
    fat: Math.round(r.fat ?? 0),
    carbs: Math.round(r.carbs ?? 0),
    weight: r.grams ?? 0,
  }
}

export function sbBodyToBodyLog(r: any) {
  return {
    id: r.id,
    date: r.date,
    weight: r.weight ?? 0,
    bmi: r.bmi ?? 0,
    fatPct: r.fat_percent ?? 0,
    musclePct: r.muscle_percent ?? 0,
    waterPct: r.water_percent ?? 0,
    boneMass: r.bone_mass ?? 0,
    metabolism: r.bmr ?? 0,
    proteinPct: 0,
    bodyAge: 0,
    visceralFat: r.visceral_fat ?? 0,
    fatKg: r.fat_mass ?? 0,
    leanMass: r.lean_mass ?? 0,
    muscleKg: 0,
    proteinKg: 0,
  }
}

// ── Write helpers ─────────────────────────────────────────────────

export async function addFoodToSupabase(food: {
  name: string; cal100: number; pro100: number; fat100: number; carb100: number
}) {
  const { data, error } = await supabase.from('products').insert({
    name: food.name,
    calories: food.cal100,
    protein: food.pro100,
    fat: food.fat100,
    carbs: food.carb100,
  }).select().single()
  if (error) console.error('addFood error:', error)
  return data
}

export async function addMealToSupabase(meal: {
  name: string; calories: number; protein: number; fat: number; carbs: number; weight: number; mealType?: string
}) {
  const { data, error } = await supabase.from('food_log').insert({
    date: today(),
    product_name: meal.name,
    grams: meal.weight,
    calories: meal.calories,
    protein: meal.protein,
    fat: meal.fat,
    carbs: meal.carbs,
  }).select().single()
  if (error) console.error('addMeal error:', error)
  return data
}

export async function addBodyLogToSupabase(log: {
  weight: number; bmi: number; fatPct: number; musclePct: number; waterPct: number;
  boneMass: number; metabolism: number; visceralFat: number; fatKg: number; leanMass: number
}) {
  const { data, error } = await supabase.from('body_measurements').upsert({
    date: today(),
    weight: log.weight,
    bmi: log.bmi,
    fat_percent: log.fatPct,
    muscle_percent: log.musclePct,
    water_percent: log.waterPct,
    bone_mass: log.boneMass,
    bmr: log.metabolism,
    visceral_fat: log.visceralFat,
    fat_mass: log.fatKg,
    lean_mass: log.leanMass,
  }, { onConflict: 'date' }).select().single()
  if (error) console.error('addBodyLog error:', error)
  return data
}

export async function updateDailyGoalsInSupabase(goals: {
  calories: number; protein: number; fat: number; carbs: number
}) {
  const { error } = await supabase.from('daily_goals').upsert({
    date: today(),
    calories: goals.calories,
    protein: goals.protein,
    fat: goals.fat,
    carbs: goals.carbs,
  }, { onConflict: 'date' })
  if (error) console.error('updateGoals error:', error)
}

// ── Fetch all ─────────────────────────────────────────────────────

export async function fetchAll() {
  const [products, foodLog, bodyMeasurements, dailyGoals] = await Promise.all([
    supabase.from('products').select('*').order('created_at', { ascending: false }),
    supabase.from('food_log').select('*').order('date', { ascending: false }).limit(200),
    supabase.from('body_measurements').select('*').order('date', { ascending: false }).limit(90),
    supabase.from('daily_goals').select('*').eq('date', today()).single(),
  ])
  return {
    products: products.data ?? [],
    foodLog: foodLog.data ?? [],
    bodyMeasurements: bodyMeasurements.data ?? [],
    dailyGoals: dailyGoals.data ?? null,
  }
}

// ── Real-time hook ─────────────────────────────────────────────────

export function useSupabaseRealtime(onUpdate: (table: string, row: any) => void) {
  useEffect(() => {
    const channel = supabase
      .channel('db-changes')
      .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'food_log' },
        (payload) => onUpdate('food_log', payload.new))
      .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'products' },
        (payload) => onUpdate('products', payload.new))
      .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'body_measurements' },
        (payload) => onUpdate('body_measurements', payload.new))
      .on('postgres_changes', { event: 'UPDATE', schema: 'public', table: 'body_measurements' },
        (payload) => onUpdate('body_measurements_update', payload.new))
      .on('postgres_changes', { event: 'INSERT', schema: 'public', table: 'daily_goals' },
        (payload) => onUpdate('daily_goals', payload.new))
      .on('postgres_changes', { event: 'UPDATE', schema: 'public', table: 'daily_goals' },
        (payload) => onUpdate('daily_goals', payload.new))
      .subscribe()

    return () => { supabase.removeChannel(channel) }
  }, [onUpdate])
}
