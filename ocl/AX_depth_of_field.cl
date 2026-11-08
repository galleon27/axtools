/*
    AX Depth of Field  -  Copernicus OpenCL COP (Houdini 22)
    ---------------------------------------------------------
    Camera depth of field from a depth (Z) pass. It runs in a single
    pass: each pixel gathers the neighbours whose blur reaches it
    ("scatter-as-gather").

    Inputs
      src    : beauty, RGBA (premultiplied)
      depth  : Z pass, linear distance from the camera
               (Karma / Mantra "depth" or Pz). Values <= 0 count as sky.

    Usage
      1. Paste this into an OpenCL COP and click "Generate Signature
         from Kernel" so the inputs and parameters appear.
      2. Wire the beauty into src and the Z pass into depth.
      3. To focus on an object, sample its Z value in the viewer and
         type it into focus_distance.
      4. Turn on show_coc to check the focus plane: black is sharp,
         red is near blur, blue is far blur.

    Modes
      mode = 0  physical : thin-lens blur from fstop, focal length and
                           sensor width. unit_scale = millimetres per
                           scene unit (1000 when the scene is in metres).
      mode = 1  artist   : sharp inside +/- focus_range around the focus
                           distance, ramping to max_radius over falloff.

    Cost is about `samples` texture reads per pixel. Use 64 for preview
    and 128-256 for final renders.
*/

#bind layer src float4
#bind layer depth float
#bind layer !&dst float4

#bind parm mode            int   val=0
#bind parm focus_distance  float val=5
#bind parm fstop           float val=2.8
#bind parm focal_length    float val=50
#bind parm sensor_width    float val=36
#bind parm unit_scale      float val=1000
#bind parm focus_range     float val=0.5
#bind parm falloff         float val=5
#bind parm blur_scale      float val=1
#bind parm max_radius      float val=32
#bind parm samples         int   val=64
#bind parm jitter          int   val=1
#bind parm depth_normalized int  val=0
#bind parm near_clip       float val=0.1
#bind parm far_clip        float val=1000
#bind parm show_coc        int   val=0

#define GOLDEN_ANGLE 2.39996323f
#define SKY_DEPTH    1e7f

static float hash12(int2 p)
{
    uint h = (uint)p.x * 1597334677u ^ (uint)p.y * 3812015801u;
    h = (h ^ (h >> 16)) * 0x7feb352du;
    h = (h ^ (h >> 15)) * 0x846ca68bu;
    h ^= h >> 16;
    return (float)h * (1.0f / 4294967296.0f);
}

static float linearZ(float d, int normalized, float n, float f)
{
    if (normalized)
        d = n + d * (f - n);
    return (d <= 0.0f) ? SKY_DEPTH : d;
}

/* Signed CoC radius in pixels: negative = near field, positive = far field. */
static float cocRadius(float z, int mode, float zf, float fstop, float flen,
                       float sensor_w, float uscale, float frange, float fall,
                       float bscale, float rmax, int xres)
{
    float r;
    if (mode == 0)
    {
        float zmm  = z  * uscale;
        float zfmm = fmax(zf * uscale, flen + 1e-3f);
        float A    = flen / fmax(fstop, 1e-3f);                 // aperture diameter, mm
        float cmm  = A * flen * (zmm - zfmm) / (zmm * (zfmm - flen)); // CoC diameter on sensor
        r = 0.5f * cmm / fmax(sensor_w, 1e-3f) * (float)xres;
    }
    else
    {
        float dz = z - zf;
        float t  = smoothstep(frange, frange + fmax(fall, 1e-4f), fabs(dz));
        r = copysign(t * rmax, dz);
    }
    r *= bscale;
    return clamp(r, -rmax, rmax);
}

@KERNEL
{
    const int xres = @xres;
    const int yres = @yres;
    const int2 pc  = (int2)(@ix, @iy);

    const float zc = linearZ(@depth, @depth_normalized, @near_clip, @far_clip);
    const float cc = cocRadius(zc, @mode, @focus_distance, @fstop, @focal_length,
                               @sensor_width, @unit_scale, @focus_range, @falloff,
                               @blur_scale, @max_radius, xres);
    const float rc = fabs(cc);

    if (@show_coc)
    {
        float v = rc / fmax(@max_radius, 1e-4f);
        @dst.set(cc < 0.0f ? (float4)(v, 0.0f, 0.0f, 1.0f)
                           : (float4)(0.0f, 0.0f, v, 1.0f));
        return;
    }

    const float R = fmax(@max_radius, 0.5f);
    const int   N = max(@samples, 1);

    // Every gather sample stands for this many pixels of area.
    const float sampleArea = M_PI_F * R * R / (float)N;
    const float rot = @jitter ? hash12(pc) * 2.0f * M_PI_F : 0.0f;

    // The centre pixel always covers itself.
    float  wc   = 1.0f / (M_PI_F * fmax(rc, 0.5f) * fmax(rc, 0.5f));
    float4 acc  = @src * wc;
    float  wsum = wc;

    for (int i = 0; i < N; ++i)
    {
        float r     = R * sqrt(((float)i + 0.5f) / (float)N);
        float theta = (float)i * GOLDEN_ANGLE + rot;
        int2  ps    = (int2)(clamp(pc.x + (int)round(r * cos(theta)), 0, xres - 1),
                             clamp(pc.y + (int)round(r * sin(theta)), 0, yres - 1));

        float zs = linearZ(@depth.bufferIndex(ps), @depth_normalized, @near_clip, @far_clip);
        float cs = cocRadius(zs, @mode, @focus_distance, @fstop, @focal_length,
                             @sensor_width, @unit_scale, @focus_range, @falloff,
                             @blur_scale, @max_radius, xres);
        float rs = fabs(cs);

        // A background sample can't spread past a closer centre pixel's
        // own blur. This stops sharp foreground edges getting halos.
        if (zs > zc)
            rs = fmin(rs, rc);

        // Does this sample's disk reach the centre? (soft 1px edge)
        float cover = clamp(rs - r + 1.0f, 0.0f, 1.0f);
        if (cover <= 0.0f)
            continue;

        // Spread the sample's energy over its disk area.
        float w = cover * sampleArea / (M_PI_F * fmax(rs, 0.5f) * fmax(rs, 0.5f));

        acc  += @src.bufferIndex(ps) * w;
        wsum += w;
    }

    @dst.set(acc / wsum);
}
