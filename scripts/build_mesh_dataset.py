"""Build curated local MeSH condition dataset with entry terms and consumer synonyms.

Combines canonical MeSH descriptor identifiers, terms, and tree numbers with
NLM entry terms and project-authored consumer synonyms (e.g. 'sugar diabetes',
'brittle bones', 'weak heart').

Source: National Library of Medicine (NLM), Medical Subject Headings (MeSH).
License: Public Domain (U.S. Government work under 17 U.S.C. § 105).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "src" / "api" / "resources" / "mesh_conditions.json"

RAW_CONCEPTS: list[dict[str, Any]] = [
    # --- Cardiovascular ---
    {
        "mesh_id": "D002318",
        "term": "Cardiovascular Diseases",
        "tree_codes": ["C14"],
        "synonyms": ["Cardiovascular Disease", "Heart and Vascular Diseases", "Circulatory System Disease"],
    },
    {
        "mesh_id": "D006331",
        "term": "Heart Diseases",
        "tree_codes": ["C14.280"],
        "synonyms": ["Heart Disease", "Cardiac Diseases", "Cardiac Disorder"],
    },
    {
        "mesh_id": "D009203",
        "term": "Myocardial Infarction",
        "tree_codes": ["C14.280.647.500"],
        "synonyms": ["Heart Attack", "Heart Attacks", "Cardiac Infarction", "Myocardial Infarct", "Coronary Thrombosis", "Acute Coronary Syndrome"],
    },
    {
        "mesh_id": "D000077202",
        "term": "ST Elevation Myocardial Infarction",
        "tree_codes": ["C14.280.647.500.750"],
        "synonyms": ["STEMI", "ST Segment Elevation Myocardial Infarction", "Transmural Myocardial Infarction"],
    },
    {
        "mesh_id": "D000077203",
        "term": "Non-ST Elevation Myocardial Infarction",
        "tree_codes": ["C14.280.647.500.500"],
        "synonyms": ["NSTEMI", "Non-ST Segment Elevation Myocardial Infarction", "Subendocardial Myocardial Infarction"],
    },
    {
        "mesh_id": "D006333",
        "term": "Heart Failure",
        "tree_codes": ["C14.280.434"],
        "synonyms": ["Congestive Heart Failure", "CHF", "Weak Heart", "Cardiac Failure", "Heart Failure, Congestive", "Left Sided Heart Failure", "Right Sided Heart Failure"],
    },
    {
        "mesh_id": "D054143",
        "term": "Heart Failure, Systolic",
        "tree_codes": ["C14.280.434.500"],
        "synonyms": ["Systolic Heart Failure", "HFrEF", "Heart Failure with Reduced Ejection Fraction"],
    },
    {
        "mesh_id": "D054144",
        "term": "Heart Failure, Diastolic",
        "tree_codes": ["C14.280.434.250"],
        "synonyms": ["Diastolic Heart Failure", "HFpEF", "Heart Failure with Preserved Ejection Fraction"],
    },
    {
        "mesh_id": "D001145",
        "term": "Arrhythmias, Cardiac",
        "tree_codes": ["C14.280.067"],
        "synonyms": ["Irregular Heartbeat", "Heart Arrhythmia", "Cardiac Arrhythmia", "Dysrhythmia", "Heart Palpitations"],
    },
    {
        "mesh_id": "D001281",
        "term": "Atrial Fibrillation",
        "tree_codes": ["C14.280.067.198"],
        "synonyms": ["AFib", "A-fib", "Auricular Fibrillation", "AF", "Atrial Fib"],
    },
    {
        "mesh_id": "D006973",
        "term": "Hypertension",
        "tree_codes": ["C14.907.489"],
        "synonyms": ["High Blood Pressure", "HTN", "Arterial Hypertension", "High Tension"],
    },
    {
        "mesh_id": "D000787",
        "term": "Angina Pectoris",
        "tree_codes": ["C14.280.647.124"],
        "synonyms": ["Angina", "Chest Pain, Cardiac", "Coronary Artery Angina", "Stenocardia"],
    },
    {
        "mesh_id": "D050197",
        "term": "Atherosclerosis",
        "tree_codes": ["C14.907.137.126"],
        "synonyms": ["Hardening of the Arteries", "Arteriosclerotic Vascular Disease", "Atheroma", "Plaque, Arterial"],
    },
    {
        "mesh_id": "D001161",
        "term": "Arteriosclerosis",
        "tree_codes": ["C14.907.137"],
        "synonyms": ["Arterial Hardening", "Arteriosclerotic Disease"],
    },
    {
        "mesh_id": "D058729",
        "term": "Peripheral Arterial Disease",
        "tree_codes": ["C14.907.617"],
        "synonyms": ["PAD", "Poor Circulation", "Peripheral Vascular Disease", "PVD", "Claudication, Intermittent"],
    },
    {
        "mesh_id": "D013927",
        "term": "Thrombosis",
        "tree_codes": ["C14.907.355.830"],
        "synonyms": ["Blood Clot", "Blood Clotting", "Thrombus"],
    },
    {
        "mesh_id": "D020246",
        "term": "Venous Thrombosis",
        "tree_codes": ["C14.907.355.830.900"],
        "synonyms": ["Deep Vein Thrombosis", "DVT", "Phlebothrombosis", "Deep Venous Thrombosis"],
    },

    # --- Neurological & Neurodegenerative ---
    {
        "mesh_id": "D009422",
        "term": "Nervous System Diseases",
        "tree_codes": ["C10"],
        "synonyms": ["Neurological Disorders", "Neurologic Diseases", "Nervous System Disorder"],
    },
    {
        "mesh_id": "D001927",
        "term": "Brain Diseases",
        "tree_codes": ["C10.228.140"],
        "synonyms": ["Brain Disorder", "Encephalopathy", "Cerebral Disorder", "Intracranial Disease"],
    },
    {
        "mesh_id": "D019636",
        "term": "Neurodegenerative Diseases",
        "tree_codes": ["C10.574"],
        "synonyms": ["Neurodegenerative Disorder", "Neurologic Degeneration", "Degenerative Neurological Disease"],
    },
    {
        "mesh_id": "D003704",
        "term": "Dementia",
        "tree_codes": ["C10.228.140.380"],
        "synonyms": ["Senility", "Cognitive Decline", "Mental Deterioration", "Senile Dementia", "Dementia Syndrome"],
    },
    {
        "mesh_id": "D000544",
        "term": "Alzheimer Disease",
        "tree_codes": ["C10.228.140.380.100"],
        "synonyms": ["Alzheimers", "Alzheimer's", "Alzheimer Dementia", "AD", "Primary Senile Degenerative Dementia", "Alzheimer Syndrome"],
    },
    {
        "mesh_id": "D000095882",
        "term": "Early-Onset Alzheimer Disease",
        "tree_codes": ["C10.228.140.380.100.250"],
        "synonyms": ["Early Onset Alzheimer", "Presenile Alzheimer Disease", "EOAD"],
    },
    {
        "mesh_id": "D000095883",
        "term": "Late-Onset Alzheimer Disease",
        "tree_codes": ["C10.228.140.380.100.500"],
        "synonyms": ["Late Onset Alzheimer", "LOAD", "Senile Dementia of Alzheimer Type"],
    },
    {
        "mesh_id": "D015140",
        "term": "Frontotemporal Dementia",
        "tree_codes": ["C10.228.140.380.300"],
        "synonyms": ["FTD", "Pick Disease", "Frontotemporal Lobar Degeneration", "Pick's Disease"],
    },
    {
        "mesh_id": "D020961",
        "term": "Lewy Body Disease",
        "tree_codes": ["C10.228.140.380.400"],
        "synonyms": ["Dementia with Lewy Bodies", "DLB", "Lewy Body Dementia", "Diffuse Lewy Body Disease"],
    },
    {
        "mesh_id": "D015162",
        "term": "Vascular Dementia",
        "tree_codes": ["C10.228.140.380.800"],
        "synonyms": ["Multi-Infarct Dementia", "Vascular Cognitive Impairment", "Post-Stroke Dementia"],
    },
    {
        "mesh_id": "D060825",
        "term": "Cognitive Dysfunction",
        "tree_codes": ["C10.228.140.380.200"],
        "synonyms": ["Mild Cognitive Impairment", "MCI", "Cognitive Impairment", "Mental Fatigue", "Brain Fog"],
    },
    {
        "mesh_id": "D003072",
        "term": "Cognition Disorders",
        "tree_codes": ["C10.597"],
        "synonyms": ["Cognitive Disorder", "Cognitive Deficit"],
    },
    {
        "mesh_id": "D008569",
        "term": "Memory Disorders",
        "tree_codes": ["C10.597.600"],
        "synonyms": ["Memory Disorder", "Memory Loss", "Impaired Memory", "Memory Impairment"],
    },
    {
        "mesh_id": "D000647",
        "term": "Amnesia",
        "tree_codes": ["C10.597.600.100", "C10.228.140.079"],
        "synonyms": ["Amnesic Syndrome", "Loss of Memory"],
    },
    {
        "mesh_id": "D010300",
        "term": "Parkinson Disease",
        "tree_codes": ["C10.574.673"],
        "synonyms": ["Parkinsons", "Parkinson's", "Shaking Palsy", "PD", "Paralysis Agitans", "Parkinsonian Syndrome"],
    },
    {
        "mesh_id": "D000687",
        "term": "Amyotrophic Lateral Sclerosis",
        "tree_codes": ["C10.574.500"],
        "synonyms": ["ALS", "Lou Gehrig's Disease", "Lou Gehrig Disease", "Motor Neuron Disease", "Charcot Disease"],
    },
    {
        "mesh_id": "D009103",
        "term": "Multiple Sclerosis",
        "tree_codes": ["C10.314.350"],
        "synonyms": ["MS", "Disseminated Sclerosis", "Multiple Sclerosis, Relapsing-Remitting", "RRMS"],
    },
    {
        "mesh_id": "D020521",
        "term": "Stroke",
        "tree_codes": ["C10.228.140.790"],
        "synonyms": ["Brain Attack", "Cerebrovascular Accident", "CVA", "Apoplexy", "Stroke, Acute"],
    },
    {
        "mesh_id": "D000083242",
        "term": "Ischemic Stroke",
        "tree_codes": ["C10.228.140.790.500"],
        "synonyms": ["Cerebral Infarction", "Brain Ischemia", "Ischemic Infarction"],
    },
    {
        "mesh_id": "D004827",
        "term": "Epilepsy",
        "tree_codes": ["C10.228.140.490"],
        "synonyms": ["Seizures", "Seizure Disorder", "Falling Sickness", "Epileptic Fits", "Convulsive Disorder"],
    },
    {
        "mesh_id": "D008881",
        "term": "Migraine Disorders",
        "tree_codes": ["C10.228.140.546.399.750"],
        "synonyms": ["Migraine", "Migraines", "Sick Headache", "Hemicrania", "Ocular Migraine"],
    },
    {
        "mesh_id": "D006261",
        "term": "Headache",
        "tree_codes": ["C10.228.140.546.399"],
        "synonyms": ["Headaches", "Cephalea", "Cranial Pain", "Head Pain"],
    },

    # --- Oncology / Cancers ---
    {
        "mesh_id": "D009369",
        "term": "Neoplasms",
        "tree_codes": ["C04"],
        "synonyms": ["Cancer", "Tumor", "Tumour", "Malignancy", "Neoplasm", "Malignant Neoplasms", "Benign Neoplasm"],
    },
    {
        "mesh_id": "D008175",
        "term": "Lung Neoplasms",
        "tree_codes": ["C04.588.894.797.520"],
        "synonyms": ["Lung Cancer", "Pulmonary Cancer", "Pulmonary Neoplasm", "Malignant Lung Tumor"],
    },
    {
        "mesh_id": "D002289",
        "term": "Carcinoma, Non-Small-Cell Lung",
        "tree_codes": ["C04.588.894.797.520.100"],
        "synonyms": ["Non-Small Cell Lung Cancer", "NSCLC", "Non Small Cell Lung Carcinoma", "Non-Small Cell Lung Carcinoma"],
    },
    {
        "mesh_id": "D018288",
        "term": "Carcinoma, Small Cell",
        "tree_codes": ["C04.588.894.797.520.200"],
        "synonyms": ["Small Cell Lung Cancer", "SCLC", "Oat Cell Carcinoma"],
    },
    {
        "mesh_id": "D001943",
        "term": "Breast Neoplasms",
        "tree_codes": ["C04.588.180"],
        "synonyms": ["Breast Cancer", "Mammary Cancer", "Malignant Breast Tumor", "Breast Carcinoma"],
    },
    {
        "mesh_id": "D064726",
        "term": "Triple Negative Breast Neoplasms",
        "tree_codes": ["C04.588.180.750"],
        "synonyms": ["Triple Negative Breast Cancer", "TNBC"],
    },
    {
        "mesh_id": "D015179",
        "term": "Colorectal Neoplasms",
        "tree_codes": ["C04.588.274.476.411"],
        "synonyms": ["Colon Cancer", "Bowel Cancer", "Colorectal Cancer", "Rectal Cancer", "Large Bowel Cancer"],
    },
    {
        "mesh_id": "D003110",
        "term": "Colonic Neoplasms",
        "tree_codes": ["C04.588.274.476.411.200"],
        "synonyms": ["Colon Tumor", "Cancer of Colon", "Colonic Cancer"],
    },
    {
        "mesh_id": "D011471",
        "term": "Prostatic Neoplasms",
        "tree_codes": ["C04.588.945.440.770"],
        "synonyms": ["Prostate Cancer", "Prostatic Cancer", "Prostate Carcinoma", "Malignant Prostate Tumor"],
    },
    {
        "mesh_id": "D012878",
        "term": "Skin Neoplasms",
        "tree_codes": ["C04.588.805"],
        "synonyms": ["Skin Cancer", "Cutaneous Neoplasms", "Malignant Skin Tumor"],
    },
    {
        "mesh_id": "D008545",
        "term": "Melanoma",
        "tree_codes": ["C04.588.805.500"],
        "synonyms": ["Malignant Melanoma", "Melanocarcinoma", "Skin Melanoma"],
    },
    {
        "mesh_id": "D010051",
        "term": "Ovarian Neoplasms",
        "tree_codes": ["C04.588.322.455"],
        "synonyms": ["Ovarian Cancer", "Ovary Cancer", "Malignant Ovarian Tumor"],
    },
    {
        "mesh_id": "D010190",
        "term": "Pancreatic Neoplasms",
        "tree_codes": ["C04.588.274.761"],
        "synonyms": ["Pancreatic Cancer", "Pancreas Cancer", "Malignant Pancreatic Tumor"],
    },
    {
        "mesh_id": "D007938",
        "term": "Leukemia",
        "tree_codes": ["C04.557.337"],
        "synonyms": ["Blood Cancer", "Leukaemia", "Leukemic Disease"],
    },
    {
        "mesh_id": "D008223",
        "term": "Lymphoma",
        "tree_codes": ["C04.557.386"],
        "synonyms": ["Lymph Node Cancer", "Lymphoid Neoplasms", "Malignant Lymphoma"],
    },

    # --- Metabolic & Endocrine ---
    {
        "mesh_id": "D004700",
        "term": "Endocrine System Diseases",
        "tree_codes": ["C19"],
        "synonyms": ["Endocrine Disease", "Endocrinopathy", "Endocrine Disorder"],
    },
    {
        "mesh_id": "D008659",
        "term": "Metabolic Diseases",
        "tree_codes": ["C18.452"],
        "synonyms": ["Metabolic Disorder", "Metabolism Diseases", "Inborn Errors of Metabolism"],
    },
    {
        "mesh_id": "D044623",
        "term": "Glucose Metabolism Disorders",
        "tree_codes": ["C18.452.394"],
        "synonyms": ["Impaired Glucose Metabolism", "Dysglycemia"],
    },
    {
        "mesh_id": "D003920",
        "term": "Diabetes Mellitus",
        "tree_codes": ["C18.452.394.750"],
        "synonyms": ["Sugar Diabetes", "Diabetes", "Diabetic", "Hyperglycemia, Chronic", "High Blood Sugar"],
    },
    {
        "mesh_id": "D003922",
        "term": "Diabetes Mellitus, Type 1",
        "tree_codes": ["C18.452.394.750.149"],
        "synonyms": ["Type 1 Diabetes", "Juvenile Diabetes", "Insulin-Dependent Diabetes", "T1D", "T1DM", "Early Onset Diabetes"],
    },
    {
        "mesh_id": "D003924",
        "term": "Diabetes Mellitus, Type 2",
        "tree_codes": ["C18.452.394.750.298"],
        "synonyms": ["Type 2 Diabetes", "Adult-Onset Diabetes", "Type II Diabetes", "T2D", "T2DM", "Non-Insulin Dependent Diabetes", "NIDDM"],
    },
    {
        "mesh_id": "D016640",
        "term": "Diabetes, Gestational",
        "tree_codes": ["C18.452.394.750.418"],
        "synonyms": ["Gestational Diabetes", "Pregnancy Diabetes", "GDM"],
    },
    {
        "mesh_id": "D009765",
        "term": "Obesity",
        "tree_codes": ["C18.654.726.500"],
        "synonyms": ["Overweight", "Obese", "Adiposity", "Excess Body Weight", "Corpulence"],
    },
    {
        "mesh_id": "D006937",
        "term": "Hypercholesterolemia",
        "tree_codes": ["C18.452.584.500.500"],
        "synonyms": ["High Cholesterol", "Elevated Cholesterol", "Hypercholesteremia"],
    },
    {
        "mesh_id": "D013959",
        "term": "Thyroid Diseases",
        "tree_codes": ["C19.874"],
        "synonyms": ["Thyroid Disorder", "Thyropathy", "Thyroid Problem"],
    },
    {
        "mesh_id": "D007037",
        "term": "Hypothyroidism",
        "tree_codes": ["C19.874.450"],
        "synonyms": ["Underactive Thyroid", "Low Thyroid", "Thyroid Deficiency", "Myxedema"],
    },
    {
        "mesh_id": "D006980",
        "term": "Hyperthyroidism",
        "tree_codes": ["C19.874.397"],
        "synonyms": ["Overactive Thyroid", "Graves Disease", "Thyrotoxicosis", "Toxic Goiter"],
    },
    {
        "mesh_id": "D006073",
        "term": "Gout",
        "tree_codes": ["C18.452.648.374"],
        "synonyms": ["Gouty Arthritis", "Hyperuricemia", "Podagra", "Uric Acid Arthritis"],
    },
    {
        "mesh_id": "D010024",
        "term": "Osteoporosis",
        "tree_codes": ["C05.116.099.708.618"],
        "synonyms": ["Brittle Bones", "Bone Loss", "Osteopenia", "Fragile Bones", "Bone Mineral Loss"],
    },

    # --- Respiratory ---
    {
        "mesh_id": "D012140",
        "term": "Respiratory Tract Diseases",
        "tree_codes": ["C08"],
        "synonyms": ["Respiratory Disease", "Breathing Disorders", "Pulmonary Disease"],
    },
    {
        "mesh_id": "D001249",
        "term": "Asthma",
        "tree_codes": ["C08.127.108"],
        "synonyms": ["Bronchial Asthma", "Wheezing", "Asthmatic", "Asthma, Chronic"],
    },
    {
        "mesh_id": "D029424",
        "term": "Pulmonary Disease, Chronic Obstructive",
        "tree_codes": ["C08.381.495.389"],
        "synonyms": ["COPD", "Chronic Obstructive Pulmonary Disease", "Chronic Bronchitis", "COAD"],
    },
    {
        "mesh_id": "D011656",
        "term": "Pulmonary Emphysema",
        "tree_codes": ["C08.381.495.389.500"],
        "synonyms": ["Emphysema", "Centrilobular Emphysema", "Panacinar Emphysema"],
    },
    {
        "mesh_id": "D011014",
        "term": "Pneumonia",
        "tree_codes": ["C08.381.677"],
        "synonyms": ["Lung Infection", "Chest Infection", "Pulmonary Inflammation", "Pneumonitis"],
    },
    {
        "mesh_id": "D012891",
        "term": "Sleep Apnea Syndromes",
        "tree_codes": ["C08.618.846"],
        "synonyms": ["Sleep Apnea", "Obstructive Sleep Apnea", "OSA", "Snoring", "Sleep Disordered Breathing"],
    },
    {
        "mesh_id": "D006255",
        "term": "Rhinitis, Allergic, Seasonal",
        "tree_codes": ["C08.460.699.500"],
        "synonyms": ["Hay Fever", "Allergic Rhinitis", "Seasonal Allergy", "Pollen Allergy"],
    },
    {
        "mesh_id": "D012220",
        "term": "Rhinitis",
        "tree_codes": ["C08.460.699"],
        "synonyms": ["Runny Nose", "Stuffy Nose", "Nasal Congestion", "Coryza"],
    },
    {
        "mesh_id": "D010612",
        "term": "Pharyngitis",
        "tree_codes": ["C08.730.749"],
        "synonyms": ["Sore Throat", "Throat Infection", "Pharyngeal Inflammation"],
    },
    {
        "mesh_id": "D013290",
        "term": "Streptococcal Pharyngitis",
        "tree_codes": ["C08.730.749.800"],
        "synonyms": ["Strep Throat", "Streptococcal Sore Throat", "Strep Infection"],
    },

    # --- Mental Health ---
    {
        "mesh_id": "D001523",
        "term": "Mental Disorders",
        "tree_codes": ["F03"],
        "synonyms": ["Psychiatric Disorders", "Mental Illness", "Psychological Disorders"],
    },
    {
        "mesh_id": "D003866",
        "term": "Depressive Disorder",
        "tree_codes": ["F03.600.300"],
        "synonyms": ["Depression", "Clinical Depression", "Depressive Illness", "Major Depression", "Unipolar Depression"],
    },
    {
        "mesh_id": "D001008",
        "term": "Anxiety Disorders",
        "tree_codes": ["F03.080"],
        "synonyms": ["Anxiety", "Generalized Anxiety", "Anxiety State", "Nervousness"],
    },
    {
        "mesh_id": "D016584",
        "term": "Panic Disorder",
        "tree_codes": ["F03.080.600"],
        "synonyms": ["Panic Attacks", "Panic Attack", "Agoraphobia with Panic"],
    },
    {
        "mesh_id": "D001714",
        "term": "Bipolar Disorder",
        "tree_codes": ["F03.600.100"],
        "synonyms": ["Manic Depression", "Manic Depressive Illness", "Bipolar", "Manic Episode"],
    },
    {
        "mesh_id": "D012559",
        "term": "Schizophrenia",
        "tree_codes": ["F03.700.675"],
        "synonyms": ["Schizophrenic Disorder", "Schizoaffective", "Dementia Praecox", "Psychosis"],
    },
    {
        "mesh_id": "D001289",
        "term": "Attention Deficit Disorder with Hyperactivity",
        "tree_codes": ["F03.625.094"],
        "synonyms": ["ADHD", "ADD", "Hyperactivity", "Attention Deficit Hyperactivity Disorder"],
    },
    {
        "mesh_id": "D000067877",
        "term": "Autism Spectrum Disorder",
        "tree_codes": ["F03.625.164"],
        "synonyms": ["Autism", "ASD", "Asperger", "Asperger Syndrome", "Pervasive Development Disorder"],
    },
    {
        "mesh_id": "D007030",
        "term": "Insomnia",
        "tree_codes": ["F03.870.400"],
        "synonyms": ["Sleeplessness", "Sleep Trouble", "Cannot Sleep", "Chronic Insomnia"],
    },

    # --- Gastrointestinal ---
    {
        "mesh_id": "D004066",
        "term": "Digestive System Diseases",
        "tree_codes": ["C06"],
        "synonyms": ["Digestive Diseases", "Gastrointestinal Disorders", "GI Diseases"],
    },
    {
        "mesh_id": "D005764",
        "term": "Gastroesophageal Reflux",
        "tree_codes": ["C06.405.117.119.500"],
        "synonyms": ["Acid Reflux", "GERD", "Heartburn", "Reflux Esophagitis", "Esophageal Reflux"],
    },
    {
        "mesh_id": "D010437",
        "term": "Peptic Ulcer",
        "tree_codes": ["C06.405.469.531"],
        "synonyms": ["Stomach Ulcer", "Gastric Ulcer", "Duodenal Ulcer", "Peptic Ulcer Disease"],
    },
    {
        "mesh_id": "D015212",
        "term": "Inflammatory Bowel Diseases",
        "tree_codes": ["C06.405.469.432"],
        "synonyms": ["IBD", "Bowel Inflammation"],
    },
    {
        "mesh_id": "D003424",
        "term": "Crohn Disease",
        "tree_codes": ["C06.405.469.432.327"],
        "synonyms": ["Crohns", "Crohn's", "Regional Enteritis", "Granulomatous Enteritis"],
    },
    {
        "mesh_id": "D003093",
        "term": "Colitis, Ulcerative",
        "tree_codes": ["C06.405.469.432.418"],
        "synonyms": ["Ulcerative Colitis", "UC", "Colitis"],
    },
    {
        "mesh_id": "D043183",
        "term": "Irritable Bowel Syndrome",
        "tree_codes": ["C06.405.469.475"],
        "synonyms": ["IBS", "Spastic Colon", "Mucous Colitis"],
    },
    {
        "mesh_id": "D008103",
        "term": "Liver Cirrhosis",
        "tree_codes": ["C06.552.380"],
        "synonyms": ["Cirrhosis", "Liver Fibrosis", "Hepatic Cirrhosis", "End-Stage Liver Disease"],
    },
    {
        "mesh_id": "D005234",
        "term": "Fatty Liver",
        "tree_codes": ["C06.552.241"],
        "synonyms": ["Steatohepatitis", "Hepatic Steatosis", "NAFLD", "NASH", "Fatty Liver Disease"],
    },
    {
        "mesh_id": "D002769",
        "term": "Cholelithiasis",
        "tree_codes": ["C06.130.410"],
        "synonyms": ["Gallstones", "Gall Stone", "Biliary Calculi", "Gallbladder Stones"],
    },
    {
        "mesh_id": "D006526",
        "term": "Hepatitis C",
        "tree_codes": ["C01.778.640.400"],
        "synonyms": ["Hep C", "HCV", "Hepatitis C Infection", "Chronic Hepatitis C"],
    },

    # --- Musculoskeletal & Rheumatology ---
    {
        "mesh_id": "D009140",
        "term": "Musculoskeletal Diseases",
        "tree_codes": ["C05"],
        "synonyms": ["Musculoskeletal Disorder", "Bone and Joint Diseases"],
    },
    {
        "mesh_id": "D001168",
        "term": "Arthritis",
        "tree_codes": ["C05.550.114"],
        "synonyms": ["Joint Inflammation", "Articular Rheumatism"],
    },
    {
        "mesh_id": "D010003",
        "term": "Osteoarthritis",
        "tree_codes": ["C05.550.114.606"],
        "synonyms": ["Degenerative Arthritis", "Wear-and-Tear Arthritis", "Osteoarthrosis", "Degenerative Joint Disease"],
    },
    {
        "mesh_id": "D001172",
        "term": "Rheumatoid Arthritis",
        "tree_codes": ["C05.550.114.753"],
        "synonyms": ["RA", "Rheumatoid Disease", "Atrophic Arthritis"],
    },
    {
        "mesh_id": "D001416",
        "term": "Back Pain",
        "tree_codes": ["C23.888.592.612.107"],
        "synonyms": ["Low Back Pain", "Lumbago", "Backache", "Lumbar Pain"],
    },
    {
        "mesh_id": "D018771",
        "term": "Arthralgia",
        "tree_codes": ["C23.888.592.612.053"],
        "synonyms": ["Joint Pain", "Articular Pain", "Aching Joints"],
    },

    # --- Immune & Skin ---
    {
        "mesh_id": "D007154",
        "term": "Immune System Diseases",
        "tree_codes": ["C20"],
        "synonyms": ["Immune Disorder", "Immunologic Diseases"],
    },
    {
        "mesh_id": "D001327",
        "term": "Autoimmune Diseases",
        "tree_codes": ["C20.111"],
        "synonyms": ["Autoimmune Disorder", "Autoimmunity"],
    },
    {
        "mesh_id": "D008180",
        "term": "Lupus Erythematosus, Systemic",
        "tree_codes": ["C20.111.580"],
        "synonyms": ["Lupus", "SLE", "Systemic Lupus", "Systemic Lupus Erythematosus"],
    },
    {
        "mesh_id": "D011565",
        "term": "Psoriasis",
        "tree_codes": ["C17.800.865.711"],
        "synonyms": ["Plaque Psoriasis", "Psoriatic Disease", "Psoriasis Vulgaris"],
    },
    {
        "mesh_id": "D004485",
        "term": "Eczema",
        "tree_codes": ["C17.800.815.300"],
        "synonyms": ["Atopic Dermatitis", "Eczematous Dermatitis", "Skin Rash"],
    },
    {
        "mesh_id": "D014581",
        "term": "Urticaria",
        "tree_codes": ["C17.800.865.910"],
        "synonyms": ["Hives", "Welts", "Nettle Rash", "Wheals"],
    },

    # --- Renal & Urological ---
    {
        "mesh_id": "D007674",
        "term": "Kidney Diseases",
        "tree_codes": ["C12.777.419"],
        "synonyms": ["Kidney Disease", "Renal Disease", "Kidney Disorder", "Nephropathy"],
    },
    {
        "mesh_id": "D051436",
        "term": "Renal Insufficiency, Chronic",
        "tree_codes": ["C12.777.419.780.750"],
        "synonyms": ["Chronic Kidney Disease", "CKD", "Kidney Failure", "Renal Failure", "End Stage Renal Disease", "ESRD"],
    },
    {
        "mesh_id": "D053201",
        "term": "Urinary Bladder, Overactive",
        "tree_codes": ["C12.777.894.880"],
        "synonyms": ["Overactive Bladder", "OAB", "Urinary Urgency"],
    },
    {
        "mesh_id": "D004775",
        "term": "Enuresis",
        "tree_codes": ["F03.625.300"],
        "synonyms": ["Bedwetting", "Urinary Incontinence, Nocturnal", "Nocturnal Enuresis"],
    },

    # --- Infections ---
    {
        "mesh_id": "D018805",
        "term": "Sepsis",
        "tree_codes": ["C01.252.810"],
        "synonyms": ["Blood Poisoning", "Septicemia", "Severe Sepsis", "Septic Shock"],
    },
    {
        "mesh_id": "D013742",
        "term": "Tetanus",
        "tree_codes": ["C01.252.410.890"],
        "synonyms": ["Lockjaw", "Clostridium Tetani Infection"],
    },
    {
        "mesh_id": "D014861",
        "term": "Whooping Cough",
        "tree_codes": ["C01.252.100.875"],
        "synonyms": ["Pertussis", "Bordetella Pertussis Infection"],
    },
    {
        "mesh_id": "D006562",
        "term": "Herpes Zoster",
        "tree_codes": ["C01.925.782.350"],
        "synonyms": ["Shingles", "Zoster", "Zona"],
    },
    {
        "mesh_id": "D006560",
        "term": "Herpes Labialis",
        "tree_codes": ["C01.925.782.300"],
        "synonyms": ["Cold Sore", "Fever Blister", "Oral Herpes"],
    },
    {
        "mesh_id": "D010033",
        "term": "Otitis Media",
        "tree_codes": ["C09.218.705.663"],
        "synonyms": ["Ear Infection", "Middle Ear Infection", "Tympanitis"],
    },
    {
        "mesh_id": "D003231",
        "term": "Conjunctivitis",
        "tree_codes": ["C11.187.183"],
        "synonyms": ["Pink Eye", "Conjunctival Inflammation", "Red Eye"],
    },
    {
        "mesh_id": "D013281",
        "term": "Stomatitis, Aphthous",
        "tree_codes": ["C07.465.815.800"],
        "synonyms": ["Canker Sore", "Aphthous Ulcer", "Aphthae"],
    },

    # --- Hematology ---
    {
        "mesh_id": "D000740",
        "term": "Anemia",
        "tree_codes": ["C15.378.100"],
        "synonyms": ["Low Iron", "Iron Deficiency", "Low Red Blood Count", "Anaemia"],
    },

    # --- Ophthalmology ---
    {
        "mesh_id": "D005901",
        "term": "Glaucoma",
        "tree_codes": ["C11.510.370"],
        "synonyms": ["Ocular Hypertension", "High Eye Pressure", "Optic Nerve Damage"],
    },
    {
        "mesh_id": "D008268",
        "term": "Macular Degeneration",
        "tree_codes": ["C11.768.585.439"],
        "synonyms": ["AMD", "Age-Related Macular Degeneration", "Macular Dystrophy"],
    },
    {
        "mesh_id": "D000553",
        "term": "Amblyopia",
        "tree_codes": ["C11.768.100"],
        "synonyms": ["Lazy Eye", "Dull Sight"],
    },

    # --- Signs & Symptoms ---
    {
        "mesh_id": "D010146",
        "term": "Pain",
        "tree_codes": ["C23.888.592.612"],
        "synonyms": ["Chronic Pain", "Physical Pain", "Ache"],
    },
    {
        "mesh_id": "D002637",
        "term": "Chest Pain",
        "tree_codes": ["C23.888.592.612.200"],
        "synonyms": ["Chest Discomfort", "Thoracic Pain"],
    },
    {
        "mesh_id": "D005221",
        "term": "Fatigue",
        "tree_codes": ["C23.888.369"],
        "synonyms": ["Tiredness", "Exhaustion", "Chronic Fatigue", "Lethargy"],
    },
    {
        "mesh_id": "D004487",
        "term": "Edema",
        "tree_codes": ["C23.888.592.350"],
        "synonyms": ["Swelling", "Fluid Retention", "Water Retention", "Swollen Feet", "Swollen Legs", "Dropsy"],
    },
    {
        "mesh_id": "D004417",
        "term": "Dyspnea",
        "tree_codes": ["C23.888.852"],
        "synonyms": ["Shortness of Breath", "Breathlessness", "Difficulty Breathing", "Labored Breathing"],
    },
    {
        "mesh_id": "D014012",
        "term": "Tinnitus",
        "tree_codes": ["C23.888.592.763.850"],
        "synonyms": ["Ringing in Ears", "Ear Ringing", "Buzzing in Ears", "Ear Buzzing"],
    },
    {
        "mesh_id": "D000505",
        "term": "Alopecia",
        "tree_codes": ["C17.800.428"],
        "synonyms": ["Hair Loss", "Baldness", "Thinning Hair", "Alopecia Areata"],
    },
]


def build_mesh_dataset() -> list[dict[str, Any]]:
    # 1. Index concepts by tree code and id
    by_tree_code: dict[str, dict[str, Any]] = {}
    by_id: dict[str, dict[str, Any]] = {}

    for item in RAW_CONCEPTS:
        by_id[item["mesh_id"]] = item
        for code in item.get("tree_codes", []):
            by_tree_code[code] = item

    # 2. Build broader and narrower relationships
    concepts_out: list[dict[str, Any]] = []

    for item in RAW_CONCEPTS:
        mesh_id = item["mesh_id"]
        term = item["term"]
        tree_codes = item.get("tree_codes", [])

        broader_map: dict[str, dict[str, str]] = {}
        narrower_map: dict[str, dict[str, str]] = {}

        for code in tree_codes:
            # Broader: parent code is code without the last .segment
            parts = code.split(".")
            for i in range(len(parts) - 1, 0, -1):
                parent_code = ".".join(parts[:i])
                if parent_code in by_tree_code:
                    parent = by_tree_code[parent_code]
                    if parent["mesh_id"] != mesh_id and parent["mesh_id"] not in broader_map:
                        broader_map[parent["mesh_id"]] = {
                            "mesh_id": parent["mesh_id"],
                            "term": parent["term"],
                            "tree_code": parent_code,
                        }
                    break  # Immediate parent found

            # Narrower: look for children whose tree code starts with code + "."
            prefix = code + "."
            for other_code, other_concept in by_tree_code.items():
                if other_code.startswith(prefix) and other_concept["mesh_id"] != mesh_id:
                    # check if it is direct child or grandchild
                    sub_part = other_code[len(prefix):]
                    if "." not in sub_part:  # direct child
                        if other_concept["mesh_id"] not in narrower_map:
                            narrower_map[other_concept["mesh_id"]] = {
                                "mesh_id": other_concept["mesh_id"],
                                "term": other_concept["term"],
                                "tree_code": other_code,
                            }

        concepts_out.append(
            {
                "mesh_id": mesh_id,
                "term": term,
                "tree_codes": tree_codes,
                "synonyms": item.get("synonyms", []),
                "broader": list(broader_map.values()),
                "narrower": list(narrower_map.values()),
                "has_children": len(narrower_map) > 0,
            }
        )

    return concepts_out


def main() -> None:
    dataset = build_mesh_dataset()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(dataset, f, indent=2, ensure_ascii=False)
    print(f"Wrote {len(dataset)} MeSH concepts to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
