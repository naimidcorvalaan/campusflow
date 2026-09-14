"""Presentation navigation and stable mounted regions; no business state."""
from contextlib import contextmanager, nullcontext
import html

VIEWS = ('今天', '时间线', '材料估时')
VIEW_KEY = 'cf_workspace_view'


def view_label(view):
    # Keep the existing navigation ID and native button key on hot reload.
    return '任务估时' if view == '材料估时' else view


class _DrawerRerun(BaseException):
    """Stop only the drawer render, preserving native Streamlit semantics."""


class DeferredDrawerRerun:
    """Run the existing drawer action before widgets, rerun after their mount.

    File uploader state cannot be assigned through session_state. Aborting the
    whole page from the drawer removes its upload widget and loses the server
    file even while the browser still shows a stale filename. This adapter
    postpones only that rerun, without copying uploads or changing widget IDs.
    Profile switching still runs before the target profile's widgets exist.
    """
    def __init__(self, st):
        self.st = st
        self.requested = False

    def __getattr__(self, name):
        return getattr(self.st, name)

    def rerun(self):
        raise _DrawerRerun()

    experimental_rerun = rerun

    def __enter__(self):
        return self

    def __exit__(self, kind, value, traceback):
        if kind is _DrawerRerun:
            self.requested = True
            return True
        return False


def select_view(store, view):
    if view not in VIEWS:
        raise ValueError('Unknown workspace view')
    store[VIEW_KEY] = view
    if 'cf_environment_open' in store:
        store['cf_environment_open'] = False
    for section in ('campus', 'time'):
        if 'cf_environment_' + section + '_open' in store:
            store['cf_environment_' + section + '_open'] = False


def toggle_environment(store, section=None):
    """Only the visibility of the existing campus/clock widgets changes."""
    if section is None:
        opened = not store.get('cf_environment_open', False)
        for name in ('campus', 'time'):
            store['cf_environment_' + name + '_open'] = opened
    else:
        if section not in ('campus', 'time'):
            raise ValueError('Unknown environment control')
        key = 'cf_environment_' + section + '_open'
        store[key] = not store.get(key, False)
    store['cf_environment_open'] = any(store.get('cf_environment_' + name + '_open', False)
        for name in ('campus', 'time'))


@contextmanager
def region(st, name, visible=True):
    """Hidden regions remain mounted, including uploaders and native widgets.

    A navigation click never copies files or changes a widget key/default.
    Visibility changes only the marker inside the same native container.
    """
    with st.container() if callable(getattr(st, 'container', None)) else nullcontext():
        st.markdown('<span class="cf-region-marker cf-region-{} {}"></span>'.format(
            name, '' if visible else 'cf-region-hidden'), unsafe_allow_html=True)
        yield


def quiet_button(st, label, **kwargs):
    with st.container() if callable(getattr(st, 'container', None)) else nullcontext():
        st.markdown('<span class="cf-quiet-marker"></span>', unsafe_allow_html=True)
        return st.button(label, **kwargs)


@contextmanager
def setting_row(st, label):
    columns = st.columns((1, 1.25))
    with columns[0]:
        st.markdown('<div class="cf-setting-label">{}</div>'.format(html.escape(label)), unsafe_allow_html=True)
    with columns[1]:
        yield


def render_navigation(st, brand, settings_key):
    # Minimal injected test surfaces without a sidebar still exercise handlers.
    sidebar = getattr(st, 'sidebar', None)
    with sidebar if sidebar is not None else nullcontext():
        st.markdown(brand, unsafe_allow_html=True)
        if sidebar is not None:
            view = st.session_state.get(VIEW_KEY, VIEWS[0])
            for label in VIEWS:
                if label == '材料估时':
                    st.markdown('<div class="cf-nav-section">辅助工具</div>', unsafe_allow_html=True)
                st.button(view_label(label), key='cf_nav_'+label, type='primary' if view == label else 'secondary',
                    on_click=select_view, args=(st.session_state, label))
            st.markdown('<div class="cf-nav-bottom"></div>', unsafe_allow_html=True)
        if callable(getattr(st, 'button', None)):
            if st.button('个人设置', key='personal_settings_open'):
                st.session_state[settings_key] = True
            if sidebar is not None:
                campus=st.session_state.get('p2_live_campus_select','北洋园校区')
                from src.p3_campus_registry import DEFAULT_CAMPUS_REGISTRY
                from src.spacetime_ui import campus_texture_html
                registration = next((item for item in DEFAULT_CAMPUS_REGISTRY.list_campuses() if item.display_name == campus), None)
                if registration is not None:
                    # Decorative geometry must not block first-run setup or navigation.
                    # The existing live page still owns map availability errors.
                    try:
                        texture = campus_texture_html(DEFAULT_CAMPUS_REGISTRY.get_campus_map(registration.campus_id))
                    except (OSError, ValueError):
                        texture = ''
                    if texture:
                        st.markdown(texture, unsafe_allow_html=True)
                st.markdown('<div class="cf-nav-environment">天津大学 · {}</div>'.format(html.escape(str(campus))),unsafe_allow_html=True)


def render_profile_status(st, identity, status):
    """Read the existing identity; do not initialize or persist a profile."""
    sidebar = getattr(st, 'sidebar', None)
    if sidebar is None:
        return
    if not getattr(identity, 'persistent', False):
        return
    if status == '本次内容未保存到个人档案':
        label = '个人档案 · 本次未保存'
    else:
        label = '个人档案已启用'
    with sidebar:
        st.markdown('<div class="cf-nav-profile">{}</div>'.format(label), unsafe_allow_html=True)


def render_today_texture(st, map_data, view):
    """Reuse the sidebar's real campus geometry as a noninteractive material."""
    if view != VIEWS[0] or map_data is None:
        return
    from src.spacetime_ui import campus_texture_html
    texture = campus_texture_html(map_data)
    if texture:
        st.markdown(texture.replace('class="cf-campus-imprint"', 'class="cf-today-texture"'),
            unsafe_allow_html=True)
