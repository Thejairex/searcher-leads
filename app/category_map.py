"""Mapeo de categoría libre del usuario -> tipo oficial de Google (Place Types).

Para búsquedas más precisas: en vez de mandar texto libre a Google, si la categoría
tiene un match en el catálogo oficial de Place Types, se envía `includedType`.
Google no cobra más por este filtro (es un filtro, no un campo del mask).
"""

# Categorías comunes -> tipo Google oficial (https://developers.google.com/maps/documentation/places/web-service/supported-types)
CATEGORY_MAP: dict[str, str] = {
    # Alimentos
    "restaurante": "restaurant",
    "restaurantes": "restaurant",
    "cafe": "cafe",
    "cafeteria": "cafe",
    "bar": "bar",
    "bares": "bar",
    "panaderia": "bakery",
    "pizzeria": "pizza_restaurant",
    "hamburgueseria": "hamburger_restaurant",
    "heladeria": "ice_cream_shop",
    # Salud
    "dentista": "dentist",
    "dentistas": "dentist",
    "clinica": "doctor",
    "medico": "doctor",
    "medicos": "doctor",
    "farmacia": "pharmacy",
    "veterinario": "veterinary_care",
    "veterinarios": "veterinary_care",
    "optica": "optical_store",
    # Hogar y oficios
    "plomero": "plumber",
    "plomeros": "plumber",
    "electricista": "electrician",
    "electricistas": "electrician",
    "carpintero": "general_contractor",
    "mecanico": "car_repair",
    "mecanicos": "car_repair",
    "pintor": "painter",
    # Belleza y cuidado
    "peluqueria": "hair_care",
    "peluquerias": "hair_care",
    "spa": "spa",
    # Negocios y servicios
    "abogado": "lawyer",
    "abogados": "lawyer",
    "contador": "accountant",
    "contadores": "accountant",
    "inmobiliaria": "real_estate_agency",
    "inmobiliarias": "real_estate_agency",
    "agencia de viajes": "travel_agency",
    "tecnologia": "software_company",
    "software": "software_company",
    "informatica": "software_company",
    # Comercio
    "tienda de ropa": "clothing_store",
    "zapateria": "shoe_store",
    "joyeria": "jewelry_store",
    "libreria": "book_store",
    "floreria": "florist",
    "tintoreria": "dry_cleaning",
    "ferreteria": "hardware_store",
    # Alojamiento y fitness
    "gimnasio": "gym",
    "gimnasios": "gym",
    "hotel": "hotel",
    "hoteles": "hotel",
    "hostel": "hostel",
}


def resolve_included_type(categoria: str) -> str | None:
    """Retorna el tipo Google si la categoría tiene match; si no, None."""
    if not categoria:
        return None
    key = categoria.strip().lower()
    # intento directo
    if key in CATEGORY_MAP:
        return CATEGORY_MAP[key]
    # intento quitando acentos básicos para robustez
    normalized = (
        key.replace("á", "a").replace("é", "e").replace("í", "i")
        .replace("ó", "o").replace("ú", "u").replace("ü", "u")
    )
    if normalized in CATEGORY_MAP:
        return CATEGORY_MAP[normalized]
    return None