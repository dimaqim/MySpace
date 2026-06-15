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
    defaultGrams: p.default_grams ?? undefined,
    defaultUnit: p.default_unit ?? 'г',
  }
}

export function sbFoodLogToMeal(r: any) {
  return {
    id: r.id,
    date: r.date,
    name: r.product_name,
    mealType: r.meal_type
      ? String(r.meal_type).charAt(0).toUpperCase() + String(r.meal_type).slice(1)
      : 'Без приёма',
    calories: Math.round(r.calories ?? 0),
    protein: Math.round(r.protein ?? 0),
    fat: Math.round(r.fat ?? 0),
    carbs: Math.round(r.carbs ?? 0),
    weight: r.grams ?? 0,
    unit: r.unit ?? 'г',
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
    proteinPct: r.protein_percent ?? 0,
    bodyAge: r.body_age ?? 0,
    visceralFat: r.visceral_fat ?? 0,
    fatKg: r.fat_mass ?? 0,
    leanMass: r.lean_mass ?? 0,
    muscleKg: r.muscle_mass ?? 0,
    proteinKg: r.protein_kg ?? 0,
  }
}

// ── Delete / update helpers ───────────────────────────────────────

export async function deleteMealFromSupabase(id: string) {
  const { error } = await supabase.from('food_log').delete().eq('id', id)
  if (error) console.error('deleteMeal error:', error)
}

export async function deleteProductFromSupabase(id: string) {
  const { error } = await supabase.from('products').delete().eq('id', id)
  if (error) console.error('deleteProduct error:', error)
}

export async function deleteBodyMeasurementForDate(date: string) {
  const { error } = await supabase.from('body_measurements').delete().eq('date', date)
  if (error) console.error('deleteBody error:', error)
}

export async function updateProductInSupabase(id: string, p: {
  name?: string; cal100?: number; pro100?: number; fat100?: number; carb100?: number
}) {
  const patch: any = {}
  if (p.name !== undefined) patch.name = p.name
  if (p.cal100 !== undefined) patch.calories = p.cal100
  if (p.pro100 !== undefined) patch.protein = p.pro100
  if (p.fat100 !== undefined) patch.fat = p.fat100
  if (p.carb100 !== undefined) patch.carbs = p.carb100
  const { error } = await supabase.from('products').update(patch).eq('id', id)
  if (error) console.error('updateProduct error:', error)
}

// ── Write helpers ─────────────────────────────────────────────────

export async function addFoodToSupabase(food: {
  name: string; brand?: string; cal100: number; pro100: number; fat100: number; carb100: number
}) {
  const { data, error } = await supabase.from('products').insert({
    name: food.name,
    brand: food.brand || null,
    calories: food.cal100,
    protein: food.pro100,
    fat: food.fat100,
    carbs: food.carb100,
  }).select().single()
  if (error) console.error('addFood error:', error)
  return data
}

const mealTypeToBot = (mt?: string) => {
  if (!mt) return null
  const m = mt.toLowerCase()
  if (m.includes('завтрак')) return 'завтрак'
  if (m.includes('обед')) return 'обед'
  if (m.includes('ужин')) return 'ужин'
  if (m.includes('перекус')) return 'перекус'
  return null
}

export async function addMealToSupabase(meal: {
  name: string; calories: number; protein: number; fat: number; carbs: number;
  weight: number; mealType?: string; date?: string; unit?: string
}) {
  const { data, error } = await supabase.from('food_log').insert({
    date: meal.date || today(),
    product_name: meal.name,
    grams: meal.weight,
    calories: meal.calories,
    protein: meal.protein,
    fat: meal.fat,
    carbs: meal.carbs,
    meal_type: mealTypeToBot(meal.mealType),
    unit: meal.unit || 'г',
  }).select().single()
  if (error) console.error('addMeal error:', error)
  return data
}

export async function updateMealInSupabase(id: string, m: {
  weight?: number; calories?: number; protein?: number; fat?: number; carbs?: number;
  mealType?: string; date?: string
}) {
  const patch: any = {}
  if (m.weight !== undefined) patch.grams = m.weight
  if (m.calories !== undefined) patch.calories = m.calories
  if (m.protein !== undefined) patch.protein = m.protein
  if (m.fat !== undefined) patch.fat = m.fat
  if (m.carbs !== undefined) patch.carbs = m.carbs
  if (m.mealType !== undefined) patch.meal_type = mealTypeToBot(m.mealType)
  if (m.date !== undefined) patch.date = m.date
  const { error } = await supabase.from('food_log').update(patch).eq('id', id)
  if (error) console.error('updateMeal error:', error)
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
