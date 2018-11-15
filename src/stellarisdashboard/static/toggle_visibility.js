// taken from https://stackoverflow.com/a/19459887

function toggle_visibility(className) {
    var elements = document.getElementsByClassName(className);
    for (var i = 0; i < elements.length; i++) {
        elements[i].style.display = (elements[i].style.display == 'list-item' || elements[i].style.display == "") ? 'none' : 'list-item';
    }
}


function show_all_ledgeritems() {
    var elements = document.getElementsByClassName('eventitem');
    for (var i = 0; i < elements.length; i++) {
        elements[i].style.display = 'list-item';
    }
}