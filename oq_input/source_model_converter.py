"""
Convert NRML source model file to ESRI shapefile (and vice versa).
"""
import os
import argparse
import shapefile
from shapely import wkt
from argparse import RawTextHelpFormatter
from collections import OrderedDict

from openquake.nrmllib.hazard.parsers import SourceModelParser
from openquake.nrmllib.hazard.writers import SourceModelXMLWriter
from openquake.nrmllib.models import (PointSource, PointGeometry, AreaSource,
    AreaGeometry, SimpleFaultSource, SimpleFaultGeometry, ComplexFaultSource,
    ComplexFaultGeometry, IncrementalMFD, TGRMFD, NodalPlane, HypocentralDepth,
    CharacteristicSource, PlanarSurface, Point, SourceModel)

# maximum field size allowed by shapefile
FIELD_SIZE = 255
# maximum number of occurrence rates that can be stored for incremental MFD
MAX_RATES = 50
# maximum number of nodal planes
MAX_NODAL_PLANES = 20
# maximum number of hypocentral depths
MAX_HYPO_DEPTHS = 20

# each triplet contains nrmllib parameter name, shapefile field name and
# data type
BASE_PARAMS = [
    ('id', 'id', 'c'), ('name', 'name', 'c'), ('trt', 'trt', 'c'),
    ('mag_scale_rel', 'msr', 'c'), ('rupt_aspect_ratio', 'rar', 'f'),
    ('rake', 'rake', 'f')
]
GEOMETRY_PARAMS = [
    ('upper_seismo_depth', 'usd', 'f'), ('lower_seismo_depth', 'lsd', 'f'),
    ('dip', 'dip', 'f')
]
MFD_PARAMS = [
    ('min_mag', 'min_mag', 'f'), ('max_mag', 'max_mag', 'f'),
    ('a_val', 'a_val', 'f'), ('b_val', 'b_val', 'f'),
    ('bin_width', 'bin_width', 'f')
]

# shapefile specific fields
RATE_PARAMS = [('rate%s' % (i+1), 'f') for i in range(MAX_RATES)]
STRIKE_PARAMS = [('strike%s' % (i+1), 'f') for i in range(MAX_NODAL_PLANES)]
DIP_PARAMS = [('dip%s' % (i+1), 'f') for i in range(MAX_NODAL_PLANES)]
RAKE_PARAMS = [('rake%s' % (i+1), 'f') for i in range(MAX_NODAL_PLANES)]
NPW_PARAMS = [('np_weight%s' % (i+1), 'f') for i in range(MAX_NODAL_PLANES)]
HDEPTH_PARAMS = [('hd%s' % (i+1), 'f') for i in range(MAX_HYPO_DEPTHS)]
HDW_PARAMS = [('hd_weight%s' % (i+1), 'f') for i in range(MAX_HYPO_DEPTHS)]

def register_fields(w):
    """
    Register shapefile fields.
    """
    PARAMS_LIST = [BASE_PARAMS, GEOMETRY_PARAMS, MFD_PARAMS]
    for PARAMS in PARAMS_LIST:
        for _, param, dtype in PARAMS:
            w.field(param, fieldType=dtype, size=FIELD_SIZE)

    PARAMS_LIST = [
        RATE_PARAMS, STRIKE_PARAMS, DIP_PARAMS, RAKE_PARAMS, NPW_PARAMS,
        HDEPTH_PARAMS, HDW_PARAMS
    ]
    for PARAMS in PARAMS_LIST:
        for param, dtype in PARAMS:
            w.field(param, fieldType=dtype, size=FIELD_SIZE)

    # source typology
    w.field('source_type', 'C')

def expand_src_param(values, shp_params):
    """
    Expand hazardlib source attribute (defined through list of values)
    into dictionary of shapefile parameters.
    """
    if values is None:
        return dict([(key, None) for key, _ in shp_params])
    else:
        num_values = len(values)
        return dict(
            [(key, float(values[i]) if i < num_values else None)
            for i, (key, _) in enumerate(shp_params)]
        )

def check_size(values, name, MAX):
    """
    Raise error if size of a list is larger than allowed.
    """
    num_values = len(values)
    if values is not None and num_values > MAX:
        raise ValueError('Number of values in NRML file for %s'
            'is too large for being saved in shapefile.' % name)

def extract_source_params(obj, PARAMS):
    """
    Extract params from source object.
    """
    return dict(
        [(param, getattr(obj, key, None)) for key, param, _ in PARAMS]
    )

def extract_source_rates(src):
    """
    Extract source occurrence rates.
    """
    rates = getattr(src.mfd, 'occur_rates', None)
    if rates is not None:
        check_size(rates, 'occur_rates', MAX_RATES)

    return expand_src_param(rates, RATE_PARAMS)

def extract_source_nodal_planes(src):
    """
    Extract source nodal planes.
    """
    nodal_planes = getattr(src, 'nodal_plane_dist', None)
    if nodal_planes is not None:
        check_size(nodal_planes, 'nodal_plane_dist', MAX_NODAL_PLANES)

        strikes = [np.strike for np in nodal_planes]
        dips = [np.dip for np in nodal_planes]
        rakes = [np.rake for np in nodal_planes]
        np_weights = [np.probability for np in nodal_planes]

        strikes = expand_src_param(strikes, STRIKE_PARAMS)
        dips = expand_src_param(dips, DIP_PARAMS)
        rakes = expand_src_param(rakes, RAKE_PARAMS)
        np_weights = expand_src_param(np_weights, NPW_PARAMS)
    else:
        strikes = dict([(key, None) for key, _ in STRIKE_PARAMS])
        dips = dict([(key, None) for key, _ in DIP_PARAMS])
        rakes = dict([(key, None) for key, _ in RAKE_PARAMS])
        np_weights = dict([(key, None) for key, _ in NPW_PARAMS])

    return strikes, dips, rakes, np_weights

def extract_source_hypocentral_depths(src):
    """
    Extrac source hypocentral depths.
    """
    hypo_depths = getattr(src, 'hypo_depth_dist', None)
    if hypo_depths is not None:
        check_size(hypo_depths, 'hypo_depths', MAX_HYPO_DEPTHS)

        hds = [hd.depth for hd in hypo_depths]
        hdws = [hd.probability for hd in hypo_depths]

        hds = expand_src_param(hds, HDEPTH_PARAMS)
        hdsw = expand_src_param(hdws, HDW_PARAMS)
    else:
        hds = dict([(key, None) for key, _ in HDEPTH_PARAMS])
        hdsw = dict([(key, None) for key, _ in HDW_PARAMS])

    return hds, hdsw

def set_params(w, src):
    """
    Set source parameters.
    """
    params = extract_source_params(src, BASE_PARAMS)
    params.update(extract_source_params(src.geometry, GEOMETRY_PARAMS))
    params.update(extract_source_params(src.mfd, MFD_PARAMS))
    params.update(extract_source_rates(src))

    strikes, dips, rakes, np_weights = extract_source_nodal_planes(src)
    params.update(strikes)
    params.update(dips)
    params.update(rakes)
    params.update(np_weights)

    hds, hdsw = extract_source_hypocentral_depths(src)
    params.update(hds)
    params.update(hdsw)

    params['source_type'] = src.__class__.__name__

    w.record(**params)

def set_area_geometry(w, src):
    """
    Set area polygon as shapefile geometry
    """
    coords = wkt.loads(src.geometry.wkt)
    lons, lats = coords.exterior.xy

    w.poly(parts=[[[lon, lat] for lon, lat in zip(lons, lats)]])

def set_point_geometry(w, src):
    """
    Set point location as shapefile geometry.
    """
    location = wkt.loads(src.geometry.wkt)

    w.point(location.x, location.y)

def set_simple_fault_geometry(w, src):
    """
    Set simple fault coordinates as shapefile geometry.
    """
    coords = wkt.loads(src.geometry.wkt)
    lons, lats = coords.xy

    w.line(parts=[[[lon, lat] for lon, lat in zip(lons, lats)]])

def set_complex_fault_geometry(w, src):
    """
    Set complex fault coordinates as shapefile geometry.
    """
    edges = [src.geometry.top_edge_wkt]
    edges.extend(src.geometry.int_edges)
    edges.append(src.geometry.bottom_edge_wkt)

    parts = []
    for edge in edges:
        line = wkt.loads(edge)
        lons = [lon for lon, lat, depth in line.coords]
        lats = [lat for lon, lat, depth in line.coords]
        depths = [depth for lon, lat, depth in line.coords]
        parts.append([[lon, lat, depth] for lon, lat, depth in zip(lons, lats, depths)])

    w.line(parts=parts)

def save_area_srcs_to_shp(source_model):
    """
    Save area sources to ESRI shapefile.
    """
    w = shapefile.Writer(shapefile.POLYGON)
    register_fields(w)

    srcm = SourceModelParser(source_model).parse()
    for src in srcm:
        if isinstance(src, AreaSource):
            set_params(w, src)
            set_area_geometry(w, src)

    if len(w.shapes()) > 0:
        root = os.path.splitext(source_model)[0]
        w.save('%s_area' % root)

def save_point_srcs_to_shp(source_model):
    """
    Save area sources to ESRI shapefile.
    """
    w = shapefile.Writer(shapefile.POINT)
    register_fields(w)

    srcm = SourceModelParser(source_model).parse()
    for src in srcm:
        # make sure that is really a point - AreaSource extends PointSource
        if isinstance(src, PointSource) and not isinstance(src, AreaSource):
            set_params(w, src)
            set_point_geometry(w, src)

    if len(w.shapes()) > 0:
        root = os.path.splitext(source_model)[0]
        w.save('%s_point' % root)

def save_simple_fault_srcs_to_shp(source_model):
    """
    Save simple fault sources to ESRI shapefile.
    """
    w = shapefile.Writer(shapefile.POLYLINE)
    register_fields(w)

    srcm = SourceModelParser(source_model).parse()
    for src in srcm:
        # make sure that is really a SimpleFaultSource - ComplexFaultSource
        # extends SimpleFaultSource
        if isinstance(src, SimpleFaultSource) and \
            not isinstance(src, ComplexFaultSource):
            set_params(w, src)
            set_simple_fault_geometry(w, src)

    if len(w.shapes()) > 0:
        root = os.path.splitext(source_model)[0]
        w.save('%s_simple_fault' % root)

def save_complex_fault_srcs_to_shp(source_model):
    """
    Save complex fault sources to ESRI shapefile.
    """
    w = shapefile.Writer(shapefile.POLYLINEZ)
    register_fields(w)

    srcm = SourceModelParser(source_model).parse()
    for src in srcm:
        if isinstance(src, ComplexFaultSource):
            set_params(w, src)
            set_complex_fault_geometry(w, src)

    if len(w.shapes()) > 0:
        root = os.path.splitext(source_model)[0]
        w.save('%s_complex_fault' % root)

def extract_record_values(record):
    """
    Extract values from shapefile record.
    """
    src_params = []

    idx0 = 0
    PARAMS_LIST = [BASE_PARAMS, GEOMETRY_PARAMS, MFD_PARAMS]
    for PARAMS in PARAMS_LIST:
        src_params.append(dict(
            (param, record[idx0 + i])
             for i, (param, _, _) in enumerate(PARAMS)
             if record[idx0 + i].strip() !=''
        ))
        idx0 += len(PARAMS)

    PARAMS_LIST = [RATE_PARAMS, STRIKE_PARAMS, DIP_PARAMS, RAKE_PARAMS,
        NPW_PARAMS, HDEPTH_PARAMS, HDW_PARAMS]
    for PARAMS in PARAMS_LIST:
        src_params.append(OrderedDict(
            (param, record[idx0 + i])
            for i, (param, _) in enumerate(PARAMS)
            if record[idx0 + i].strip() !=''
        ))
        idx0 += len(PARAMS)

    (src_base_params, geometry_params, mfd_params, rate_params,
            strike_params, dip_params, rake_params, npw_params, hd_params,
            hdw_params) = src_params

    return (src_base_params, geometry_params, mfd_params, rate_params,
        strike_params, dip_params, rake_params, npw_params, hd_params,
        hdw_params)

def create_nodal_plane_dist(strikes, dips, rakes, weights):
    """
    Create nrmllib nodal plane distribution
    """
    nodal_planes = []
    for s, d, r, w in \
        zip(strikes.values(), dips.values(), rakes.values(), weights.values()):
        nodal_planes.append(NodalPlane(w, s, d, r))

    return nodal_planes

def create_hypocentral_depth_dist(hypo_depths, hypo_depth_weights):
    """
    Create nrmllib hypocentral depth distribution
    """
    hds = []
    for d, w in zip(hypo_depths.values(), hypo_depth_weights.values()):
        hds.append(HypocentralDepth(w, d))

    return hds

def create_mfd(mfd_params, rate_params):
    """
    Create nrmllib mfd (either incremental or truncated GR)
    """
    if 'min_mag' and 'bin_width' in mfd_params.keys():
        # incremental MFD
        rates = [v for v in rate_params.values()]
        return IncrementalMFD(
            mfd_params['min_mag'], mfd_params['bin_width'], rates
        )
    else:
        # truncated GR
        return TGRMFD(mfd_params['a_val'], mfd_params['b_val'],
            mfd_params['min_mag'], mfd_params['max_mag'])

def create_area_geometry(shape, geometry_params):
    """
    Create nrmllib area geometry.
    """
    wkt = 'POLYGON((%s))' % ','.join(
        ['%s %s' % (lon, lat) for lon, lat in shape.points]
    )

    geo = AreaGeometry(
        wkt, geometry_params['upper_seismo_depth'],
        geometry_params['lower_seismo_depth']
    )

    return geo

def create_point_geometry(shape, geometry_params):
    """
    Create nrmllib point geometry.
    """
    assert len(shape.points) == 1
    lon, lat = shape.points[0]

    wkt = 'POINT(%s %s)' % (lon, lat)

    geo = PointGeometry(
        wkt, geometry_params['upper_seismo_depth'],
        geometry_params['lower_seismo_depth']
    )

    return geo

def create_simple_fault_geometry(shape, geometry_params):
    """
    Create nrmllib simple fault geometry.
    """
    wkt = 'LINESTRING(%s)' % ','.join(
        ['%s %s' % (lon, lat) for lon, lat in shape.points]
    )

    geo = SimpleFaultGeometry(
        wkt=wkt, dip=geometry_params['dip'],
        upper_seismo_depth=geometry_params['upper_seismo_depth'],
        lower_seismo_depth=geometry_params['lower_seismo_depth']
    )

    return geo

def create_complex_fault_geometry(shape, geometry_params):
    """
    Create nrmllib complex fault geometry.
    """
    edges = []
    for i, idx in enumerate(shape.parts):
        idx_start = idx
        idx_end = shape.parts[i + 1] if i + 1 < len(shape.parts) else None
        wkt = 'LINESTRING(%s)' % ','.join(
            ['%s %s %s' % 
             (lon, lat, depth) for (lon, lat), depth in zip(
             shape.points[idx_start: idx_end],
             shape.z[idx_start: idx_end]
             )
            ]
        )
        edges.append(wkt)

    top_edge = edges[0]
    bottom_edge = edges[-1]

    int_edges = None
    if len(edges) > 2:
        int_edges = [edges[i] for i in range(1, len(edges) - 1)]

    geo = ComplexFaultGeometry(top_edge, bottom_edge, int_edges)

    return geo

def create_nrml_area(shape, record):
    """
    Create NRML area source from shape and record data.
    """
    (src_base_params, geometry_params, mfd_params, rate_params,
            strike_params, dip_params, rake_params, npw_params, hd_params,
            hdw_params) = extract_record_values(record)

    params = src_base_params

    params['nodal_plane_dist'] = create_nodal_plane_dist(
        strike_params, dip_params, rake_params, npw_params
    )

    params['hypo_depth_dist'] = create_hypocentral_depth_dist(
        hd_params, hdw_params
    )

    params['mfd'] = create_mfd(mfd_params, rate_params)

    params['geometry'] = create_area_geometry(shape, geometry_params)

    return AreaSource(**params)

def create_nrml_point(shape, record):
    """
    Create NRML point source from shape and record data.
    """
    (src_base_params, geometry_params, mfd_params, rate_params,
            strike_params, dip_params, rake_params, npw_params, hd_params,
            hdw_params) = extract_record_values(record)

    params = src_base_params

    params['nodal_plane_dist'] = create_nodal_plane_dist(
        strike_params, dip_params, rake_params, npw_params
    )

    params['hypo_depth_dist'] = create_hypocentral_depth_dist(
        hd_params, hdw_params
    )

    params['mfd'] = create_mfd(mfd_params, rate_params)

    params['geometry'] = create_point_geometry(shape, geometry_params)

    return PointSource(**params)

def create_nrml_simple_fault(shape, record):
    """
    Create NRML simple fault source from shape and record data.
    """
    (src_base_params, geometry_params, mfd_params, rate_params,
            strike_params, dip_params, rake_params, npw_params, hd_params,
            hdw_params) = extract_record_values(record)

    params = src_base_params

    params['mfd'] = create_mfd(mfd_params, rate_params)

    params['geometry'] = create_simple_fault_geometry(shape, geometry_params)

    return SimpleFaultSource(**params)

def create_nrml_complex_fault(shape, record):
    """
    Create NRML complex fault source from shape and record data.
    """
    (src_base_params, geometry_params, mfd_params, rate_params,
            strike_params, dip_params, rake_params, npw_params, hd_params,
            hdw_params) = extract_record_values(record)

    params = src_base_params

    params['mfd'] = create_mfd(mfd_params, rate_params)

    params['geometry'] = create_complex_fault_geometry(shape, geometry_params)

    return ComplexFaultSource(**params)

def nrml2shp(source_model):
    """
    Convert NRML source model file to ESRI shapefile
    """
    save_area_srcs_to_shp(source_model)
    save_point_srcs_to_shp(source_model)
    save_simple_fault_srcs_to_shp(source_model)
    save_complex_fault_srcs_to_shp(source_model)

def shp2nrml(source_model):
    """
    Convert source model ESRI shapefile to NRML.
    """
    sf = shapefile.Reader(source_model)

    srcs = []
    for shape, record in zip(sf.shapes(), sf.records()):
        if record[-1] == 'AreaSource':
            srcs.append(create_nrml_area(shape, record))
        if record[-1] == 'PointSource':
            srcs.append(create_nrml_point(shape, record))
        if record[-1] == 'SimpleFaultSource':
            srcs.append(create_nrml_simple_fault(shape, record))
        if record[-1] == 'ComplexFaultSource':
            srcs.append(create_nrml_complex_fault(shape, record))
    srcm = SourceModel(sources=srcs)

    root = os.path.splitext(source_model)[0]
    smw = SourceModelXMLWriter('%s.xml' % root)
    smw.serialize(srcm)

def set_up_arg_parser():
    """
    Can run as executable. To do so, set up the command line parser
    """
    parser = argparse.ArgumentParser(
        description='Convert NRML source model file to ESRI Shapefile and '
            'vice versa.\n\nTo convert from NRML to shapefile type: '
            '\npython source_model_converter.py '
            '--input-nrml-file=PATH_TO_SOURCE_MODEL_NRML_FILE. '
            '\n\nTo convert from shapefile to NRML type: '
            '\npython source_model_converter.py '
            '--input-shp-file=PATH_TO_SOURCE_MODEL_SHP_FILE ',
            add_help=False, formatter_class=RawTextHelpFormatter)
    flags = parser.add_argument_group('flag arguments')
    flags.add_argument('-h', '--help', action='help')
    group = flags.add_mutually_exclusive_group()
    group.add_argument('--input-nrml-file',
        help='path to source model NRML file',
        default=None)
    group.add_argument('--input-shp-file',
        help='path to source model ESRI shapefile (file root only - no extension)',
        default=None)

    return parser

if __name__ == "__main__":

    parser = set_up_arg_parser()
    args = parser.parse_args()

    if args.input_nrml_file:
        nrml2shp(args.input_nrml_file)
    elif args.input_shp_file:
        shp2nrml(args.input_shp_file)
    else:
        parser.print_usage()
